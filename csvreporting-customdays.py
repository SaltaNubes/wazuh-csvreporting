# Script to fetch data, generate csv report and attaches it in an email

# Steps
#  1. Fetch Wazuh Security Events (data)
#  2. Process data and generate a csv report
#  3. Send an email with csv report attached

## for creds
from dotenv import dotenv_values
import os
import sys
## for config report
import yaml
## for data report
import requests
from requests.auth import HTTPBasicAuth
import pandas as pd
import urllib3
## send mail
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

import argparse
import datetime
import logging

desde = sys.argv[1]
hasta = sys.argv[2]

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

month = datetime.datetime.now().strftime('%Y.%m')
log_filename = os.path.basename(__file__)[:-3]
log_filename = os.path.join('logs', f"{month}_{log_filename}")
log_format = '%(asctime)s %(levelname)s [%(funcName)s] %(message)s'

file_handler = logging.FileHandler(f"{log_filename}.log")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter(log_format))
log.addHandler(file_handler)

is_debug_mode = False
def debug(message):
  if is_debug_mode:
    print(message)
  log.debug(message)

def send_mail(mail_host, mail_port, mail_user, mail_pass, report, cfg_report, csv_content):
  SUBJECT = cfg_report['notification_email']['subject']
  BODY = cfg_report['notification_email']['body']
  SENDER = cfg_report['notification_email']['sender_email']
  RECEIVER = cfg_report['notification_email']['receiver_emails']
  csv_name = report+".csv"
  try:
    server = smtplib.SMTP(mail_host, mail_port)
    server.starttls()
    server.login(mail_user, mail_pass)
    msg = MIMEMultipart()
    msg['From'] = SENDER
    msg['To'] = ", ".join(RECEIVER)
    msg['Subject'] = SUBJECT
    msg.attach(MIMEText(BODY, "plain"))
    part = MIMEApplication(csv_content.encode('utf-8'), Name=csv_name)
    part['Content-Disposition'] = "attachment; filename="+csv_name
    msg.attach(part)
    server.sendmail(SENDER, RECEIVER, msg.as_string())
    log.info(f"send_mail: email sent to {RECEIVER}")
  except Exception as e:
    log.error(f"send_mail: {e}")
  finally:
    server.quit()

def gen_report(data_json, report, cfg_report):
  try:
    FIELDS = cfg_report['report_params']['fields']
    RENAME = cfg_report['report_params']['enable_field_renaming']
    NEW_FIELDS = cfg_report['report_params']['rename_fields_to']
    DT_TZ = cfg_report['time_settings']['timezone']
    DT_FORMAT = cfg_report['time_settings'].get('time_format', '%Y-%m-%d %H:%M:%S')
    AGG = cfg_report['aggregation']['enabled']
    AGG_FREQ = cfg_report['aggregation']['frequency']
    df = pd.json_normalize(data_json, max_level=10)

    if "_type" in df.columns:
      df.drop(columns=["_index", "_type", "_id", "_score"], inplace=True)
    else:
      df.drop(columns=["_index", "_id", "_score"], inplace=True)

    delete_source = [col[8::] for col in df.columns]
    df.columns = delete_source

    df['@timestamp'] = pd.to_datetime(df['@timestamp'], utc=True)
    df['@timestamp'] = df['@timestamp'].dt.tz_convert(DT_TZ)
    df['@timestamp'] = df['@timestamp'].dt.strftime(DT_FORMAT)
    df = df.reindex(columns=FIELDS)

    if RENAME:
      if len(FIELDS) == len(NEW_FIELDS):
        if len(set(NEW_FIELDS)) != len(NEW_FIELDS):
          log.error(f"gen_report: there are duplicate rename_fields_to")
          sys.exit()
        df.columns = NEW_FIELDS
      else:
        log.error(f"gen_report: verify the rename_fields_to")
        sys.exit()

    if len(set(df.columns)) != len(df.columns):
      log.error(f"gen_report: there are duplicate fields")
      sys.exit()

    if AGG:
      agg_col = df.columns.tolist()
      df[agg_col[0]] = pd.to_datetime(df[agg_col[0]], format=DT_FORMAT)
      df.fillna('Na', inplace=True)
      df = df.groupby([pd.Grouper(key=agg_col[0], freq=f"{AGG_FREQ}min")]+agg_col[1:]).size().reset_index(name="count")
      df[agg_col[0]] = df[agg_col[0]].dt.strftime(DT_FORMAT)
      log.info(f"gen_report: agg activated {len(df)} events")
    log.info(f"gen_report: csv created successfully")
    return df.to_csv(index=False)
  except Exception as e:
    log.error(f"gen_report: {e}")
    sys.exit()

def get_data_scroll(wi_url, wi_user, wi_pass, cfg_report, docs_limit, desde, hasta):
  try:
    INDEX_PATTERN = cfg_report['event_source']['index_pattern']
    QUERY_STRING = cfg_report['event_source']['query']
    REPORT_FIELDS = cfg_report['report_params']['fields']

    query_dsl = {
      "_source": { "includes": REPORT_FIELDS },
      "size": docs_limit,
      "query": {
        "bool": {
          "filter": [
            { "range": { "@timestamp": { "gte": f"{desde}", "lt": f"{hasta}" } } },
            { "query_string": { "query": QUERY_STRING } }
          ]
        }
      }
    }

    debug(f"get_data_scroll: Query DSL: {query_dsl}")
    scroll_id = None
    all_events = []

    # Initial request to start the scroll
    response = requests.post(f"{wi_url}/{INDEX_PATTERN}/_search?scroll=2m",
                             auth=HTTPBasicAuth(wi_user, wi_pass), verify=False,
                             json=query_dsl)
    if response.status_code != 200:
      log.error(f"get_data_scroll: Error starting scroll, status code {response.status_code}")
      debug(f"get_data_scroll: Error starting scroll, status code {response.status_code}, response: {response.text}")
      return []

    response_data = response.json()
    scroll_id = response_data.get('_scroll_id')
    hits = response_data.get('hits', {}).get('hits', [])
    debug(f"get_data_scroll: Initial response hits: {len(hits)}")
    all_events.extend(hits)

    # Continue scrolling until no more data
    while len(hits) > 0:
      scroll_response = requests.post(f"{wi_url}/_search/scroll",
                                      auth=HTTPBasicAuth(wi_user, wi_pass), verify=False,
                                      json={"scroll": "2m", "scroll_id": scroll_id})
      if scroll_response.status_code != 200:
        log.error(f"get_data_scroll: Error fetching scroll data, status code {scroll_response.status_code}")
        debug(f"get_data_scroll: Error fetching scroll data, status code {scroll_response.status_code}, response: {scroll_response.text}")
        break

      scroll_data = scroll_response.json()
      scroll_id = scroll_data.get('_scroll_id')
      hits = scroll_data.get('hits', {}).get('hits', [])
      debug(f"get_data_scroll: Scroll response hits: {len(hits)}")
      all_events.extend(hits)

    log.info(f"get_data_scroll: Retrieved {len(all_events)} events using scroll API")
    return all_events
  except Exception as e:
    log.error(f"get_data_scroll: {e}")
    return []

def check_file(filepath):
  if not os.path.exists(filepath):
    log.error(f"File not found: {filepath}")
    sys.exit()

def main():
  global is_debug_mode
  parser = argparse.ArgumentParser(description="Script to fetch data, generate csv report and attaches it in an email.")
  parser.add_argument('--config', type=str, required=True, help='Name Config in conf.d i.e. --config main.yml')
  parser.add_argument('--debug', action='store_true', help='Debug and test the configuration, --debug True')
  parser.add_argument('--desde', type=str, required=True, help='since time in format YYYY-mm-dd')
  parser.add_argument('--hasta', type=str, required=True, help='to time in format YYYY-mm-dd')
  if len(sys.argv) == 1:
    parser.print_help(sys.stderr)
    sys.exit(1)
  args = parser.parse_args()
  is_debug_mode = args.debug

  app_dir = os.path.dirname(os.path.realpath(__file__))

  env_file = f"{app_dir}/.env"
  creds = dotenv_values(env_file)

  conf_file = f"{app_dir}/conf.d/{args.config}"
  check_file(conf_file)

  with open(conf_file, "r") as yamlfile:
    cfg_reports = yaml.safe_load(yamlfile)

  check_file(env_file)

  #Configuracion de fechas
  desde = args.desde
  hasta = args.hasta

  WI_URL = creds['WI_URL']
  WI_USER = creds['WI_USER']
  WI_PASS = creds['WI_PASS']
  MAIL_HOST = creds['MAIL_HOST']
  MAIL_PORT = creds['MAIL_PORT']
  MAIL_USER = creds['MAIL_USER']
  MAIL_PASS = creds['MAIL_PASS']

  docs_limit = 5000

  log.info(f"main: starting")
  try:
    for report, cfg_report in cfg_reports.items():
      debug(f"main: {report} processing ++++++++++++++++++++++++")
      log.info(f"main: {report} processing ++++++++++++++++++++++++")
      data_json = get_data_scroll(WI_URL, WI_USER, WI_PASS, cfg_report, docs_limit, desde, hasta)
      if len(data_json):
        data_csv = gen_report(data_json, report, cfg_report)
        send_mail(MAIL_HOST, MAIL_PORT, MAIL_USER, MAIL_PASS, report, cfg_report, data_csv)
      else:
        rename_fields_to = cfg_report['report_params']['rename_fields_to']
        df_vacio = pd.DataFrame(columns=rename_fields_to)
        csv_data = df_vacio.to_csv(index=False, header=True)
        send_mail(MAIL_HOST, MAIL_PORT, MAIL_USER, MAIL_PASS, report, cfg_report, csv_data)
        log.info(f"main: {report}, 0 events")
  except Exception as e:
    log.error(f"main: {e}")

if __name__=="__main__":
  urllib3.disable_warnings()
  main()