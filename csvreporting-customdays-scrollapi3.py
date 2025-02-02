import os
import sys
import yaml
import requests
from requests.auth import HTTPBasicAuth
import pandas as pd
import urllib3
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import argparse
import datetime
import logging
from dotenv import dotenv_values

# Deshabilitar advertencias de SSL de urllib3
urllib3.disable_warnings()

# Configuración de logs
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

# Configuración del archivo de log
month = datetime.datetime.now().strftime('%Y.%m')
log_filename = os.path.basename(__file__)[:-3]
log_filename = os.path.join('logs', f"{month}_{log_filename}.log")
log_format = '%(asctime)s %(levelname)s [%(funcName)s] %(message)s'

file_handler = logging.FileHandler(log_filename)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter(log_format))
log.addHandler(file_handler)

is_debug_mode = False

def debug(message):
    if is_debug_mode:
        print(message)
    log.debug(message)

def send_mail(mail_host, mail_port, mail_user, mail_pass, report, cfg_report, csv_file, csv_size):
    SUBJECT = cfg_report['notification_email']['subject']
    BODY = cfg_report['notification_email']['body']
    SENDER = cfg_report['notification_email']['sender_email']
    RECEIVER = cfg_report['notification_email']['receiver_emails']

    try:
        server = smtplib.SMTP(mail_host, mail_port)
        server.starttls()
        server.login(mail_user, mail_pass)

        msg = MIMEMultipart()
        msg['From'] = SENDER
        msg['To'] = ", ".join(RECEIVER)
        msg['Subject'] = SUBJECT
        msg.attach(MIMEText(BODY, "plain"))

        with open(csv_file, 'rb') as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(csv_file))
            part['Content-Disposition'] = f"attachment; filename={os.path.basename(csv_file)}"
            msg.attach(part)

        debug(f"send_mail: Adjunto el archivo CSV '{csv_file}' al correo.")
        server.sendmail(SENDER, RECEIVER, msg.as_string())
        debug(f"send_mail: Email enviado a {RECEIVER} con archivo de {csv_size:.2f} MB")
        log.info(f"send_mail: Email enviado a {RECEIVER} con archivo de {csv_size:.2f} MB")
    except Exception as e:
        debug(f"send_mail: Error al enviar el correo - {e}")
        log.error(f"send_mail: {e}")
    finally:
        server.quit()
        if os.path.exists(csv_file):
            os.remove(csv_file)

def gen_report(data_json, report, cfg_report):
    try:
        FIELDS = cfg_report['report_params']['fields']
        RENAME = cfg_report['report_params']['enable_field_renaming']
        NEW_FIELDS = cfg_report['report_params']['rename_fields_to']
        DT_TZ = cfg_report['time_settings']['timezone']
        DT_FORMAT = cfg_report['time_settings'].get('time_format', '%Y-%m-%d %H:%M:%S')
        AGG = cfg_report['aggregation']['enabled']
        AGG_FREQ = cfg_report['aggregation']['frequency']
        csv_name = report + ".csv"

        df = pd.json_normalize(data_json, max_level=10)
        debug(f"gen_report: DataFrame inicial creado con {len(df)} filas y las siguientes columnas: {df.columns.tolist()}")
        if df.empty:
            log.info(f"gen_report: No se encontraron datos para el reporte {report}")
            return None, 0

        if '_type' in df.columns:
            df.drop(columns=["_index", "_type", "_id", "_score"], inplace=True)
        else:
            df.drop(columns=["_index", "_id", "_score"], inplace=True)

        delete_source = [col[8:] for col in df.columns]
        df.columns = delete_source

        if '@timestamp' in df.columns:
            df['@timestamp'] = pd.to_datetime(df['@timestamp'], utc=True)
            df['@timestamp'] = df['@timestamp'].dt.tz_convert(DT_TZ)
            df['@timestamp'] = df['@timestamp'].dt.strftime(DT_FORMAT)
        else:
            log.error(f"gen_report: '@timestamp' no encontrado en los datos")
            return None, 0

        df = df.reindex(columns=FIELDS)
        debug(f"gen_report: DataFrame después de reindexar con las siguientes columnas: {df.columns.tolist()}")

        if RENAME and len(FIELDS) == len(NEW_FIELDS):
            df.columns = NEW_FIELDS
            debug(f"gen_report: Columnas renombradas a: {df.columns.tolist()}")

        if AGG:
            df = df.groupby([pd.Grouper(key='@timestamp', freq=f"{AGG_FREQ}min")] + df.columns[1:].tolist()).size().reset_index(name='count')
            debug(f"gen_report: DataFrame después de la agregación con {len(df)} filas.")

        df.to_csv(csv_name, index=False)
        csv_size = os.path.getsize(csv_name) / (1024 * 1024)
        debug(f"gen_report: Archivo CSV '{csv_name}' generado con {len(df)} filas, tamaño: {csv_size:.2f} MB")
        return csv_name, csv_size
    except Exception as e:
        log.error(f"gen_report: {e}")
        return None, 0

def get_data_scroll(wi_url, wi_user, wi_pass, cfg_report, docs_limit, desde, hasta):
    try:
        INDEX_PATTERN = cfg_report['event_source']['index_pattern']
        QUERY_STRING = cfg_report['event_source']['query']
        REPORT_FIELDS = cfg_report['report_params']['fields']

        query_dsl = {
            "_source": {"includes": REPORT_FIELDS},
            "size": docs_limit,
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"@timestamp": {"gte": desde, "lt": hasta}}},
                        {"query_string": {"query": QUERY_STRING}}
                    ]
                }
            }
        }

        debug(f"get_data_scroll: Ejecutando consulta inicial con DSL: {query_dsl}")

        response = requests.post(f"{wi_url}/{INDEX_PATTERN}/_search?scroll=2m",
                                 auth=HTTPBasicAuth(wi_user, wi_pass), verify=False,
                                 json=query_dsl)
        if response.status_code != 200:
            log.error(f"get_data_scroll: Error {response.status_code}")
            return []

        response_data = response.json()
        total_hits = response_data.get('hits', {}).get('total', {}).get('value', 0)
        debug(f"get_data_scroll: Total de hits encontrados: {total_hits}")

        scroll_id = response_data.get('_scroll_id')
        hits = response_data.get('hits', {}).get('hits', [])
        all_events = hits

        while len(hits) > 0:
            scroll_response = requests.post(f"{wi_url}/_search/scroll",
                                            auth=HTTPBasicAuth(wi_user, wi_pass), verify=False,
                                            json={"scroll": "2m", "scroll_id": scroll_id})

            if scroll_response.status_code != 200:
                log.error(f"get_data_scroll: Error en scroll, código {scroll_response.status_code}")
                break

            scroll_data = scroll_response.json()
            scroll_id = scroll_data.get('_scroll_id')
            hits = scroll_data.get('hits', {}).get('hits', [])
            all_events.extend(hits)
            debug(f"get_data_scroll: Hits obtenidos en scroll actual: {len(hits)}")

        log.info(f"get_data_scroll: Total de eventos recuperados: {len(all_events)}")
        return all_events
    except Exception as e:
        log.error(f"get_data_scroll: {e}")
        return []

def main():
    global is_debug_mode
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--desde', required=True)
    parser.add_argument('--hasta', required=True)
    args = parser.parse_args()

    is_debug_mode = args.debug

    app_dir = os.path.dirname(os.path.realpath(__file__))
    env_file = f"{app_dir}/.env"
    creds = dotenv_values(env_file)

    conf_file = f"{app_dir}/conf.d/{args.config}.yml"
    with open(conf_file, 'r') as yamlfile:
        cfg_reports = yaml.safe_load(yamlfile)

    for report, cfg_report in cfg_reports.items():
        data_json = get_data_scroll(creds['WI_URL'], creds['WI_USER'], creds['WI_PASS'], cfg_report, 10000, args.desde, args.hasta)
        if data_json:
            csv_file, csv_size = gen_report(data_json, report, cfg_report)
            if csv_file:
                debug(f"main: Intentando enviar el correo con el archivo '{csv_file}'")
                send_mail(creds['MAIL_HOST'], creds['MAIL_PORT'], creds['MAIL_USER'], creds['MAIL_PASS'], report, cfg_report, csv_file, csv_size)
            else:
                debug(f"main: El archivo CSV no se generó correctamente.")

if __name__ == "__main__":
    main()
