#!/bin/bash

# Función para generar las fechas y horas en el rango especificado
generate_dates() {
    start_date=$1
    end_date=$2
    interval=$3
    dates=()
    current_date=$start_date

    # Convertir fechas a segundos desde la época (epoch) para compararlas
    start_date_epoch=$(date -d "$start_date" +%s)
    end_date_epoch=$(date -d "$end_date" +%s)

    while [ "$start_date_epoch" -le "$end_date_epoch" ]; do
        dates+=("$current_date")
        # Incrementar el intervalo especificado (en horas)
        current_date=$(date -Iseconds -d "$current_date + $interval hours")
        start_date_epoch=$(date -d "$current_date" +%s)
    done

    echo "${dates[@]}"
}

# Solicitar la fecha de inicio y fin al usuario
read -p "Introduce la fecha de inicio (YYYY-MM-DD): " start_date
read -p "Introduce la fecha de fin (YYYY-MM-DD): " end_date

# Validar el formato de las fechas
if ! [[ "$start_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || ! [[ "$end_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    echo "Error: Las fechas deben tener el formato YYYY-MM-DD"
    exit 1
fi

# Solicitar el valor de --config al usuario
read -p "Introduce el valor de --config: " config_value
config_value="${config_value}"

# Validar que no esté vacío
if [ -z "$config_value" ]; then
    echo "Error: El valor de --config no puede estar vacío."
    exit 1
fi

# Solicitar el intervalo al usuario
read -p "Introduce el intervalo en horas (4, 8, 12, 24) [24]: " interval
interval=${interval:-24}

# Validar que el intervalo sea uno de los valores permitidos
if ! [[ "$interval" =~ ^(4|8|12|24)$ ]]; then
    echo "Error: El intervalo debe ser uno de los siguientes valores: 4, 8, 12, 24"
    exit 1
fi

# Generar el rango de fechas con el intervalo especificado
dates=$(generate_dates "${start_date}T00:00:00-0600" "${end_date}T23:59:59-0600" $interval)

# Preparar las líneas de comando
commands=()
for date in $dates; do
    desde=$date
    hasta=$(date -Iseconds -d "$date + $interval hours")

    # Ajustar la hora de finalización al final del día, si es necesario
    date_day=$(date -d "$date" +%Y-%m-%d)
    hasta_day=$(date -d "$hasta" +%Y-%m-%d)
    if [[ "$date_day" != "$hasta_day" ]]; then
        hasta="${date_day}T23:59:59-0600"
    else
        # Restar un segundo para que el hasta sea HH:59:59 en lugar de HH:00:00
        hasta=$(date -Iseconds -d "$hasta - 1 second")
    fi

    cmd="python3 csvreporting-customdays-scrollapi.py --config $config_value --desde $desde --hasta $hasta --debug"
    commands+=("$cmd")
done

# Mostrar las líneas generadas
echo "Las siguientes líneas se generaron:"
for cmd in "${commands[@]}"; do
    echo "$cmd"
done

# Confirmar antes de ejecutar
read -p "¿Quieres ejecutar estos comandos? (s/n): " confirm

if [[ "$confirm" == "s" || "$confirm" == "S" ]]; then
    echo "Ejecutando comandos..."
    for cmd in "${commands[@]}"; do
        echo "Ejecutando: $cmd"
        eval "$cmd"
    done
    echo "Ejecución completada."
else
    echo "Operación cancelada."
fi
