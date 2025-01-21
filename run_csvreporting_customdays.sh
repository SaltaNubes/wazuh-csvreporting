#!/bin/bash

# Función para generar las fechas en el rango especificado
generate_dates() {
    start_date=$1
    end_date=$2
    dates=()
    current_date=$start_date

    # Convertir fechas a segundos desde la época (epoch) para compararlas
    start_date_epoch=$(date -d "$start_date" +%s)
    end_date_epoch=$(date -d "$end_date" +%s)

    while [ "$start_date_epoch" -le "$end_date_epoch" ]; do
        dates+=("$current_date")
        # Incrementar un día
        current_date=$(date -I -d "$current_date + 1 day")
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

# Validar que no esté vacío
if [ -z "$config_value" ]; then
    echo "Error: El valor de --config no puede estar vacío."
    exit 1
fi

# Generar el rango de fechas
dates=$(generate_dates "$start_date" "$end_date")

# Preparar las líneas de comando
commands=()
for date in $dates; do
    desde="${date}T00:00:00.000-0600"
    hasta="${date}T23:59:59.999-0600"
    cmd="python3 csvreporting-customdays.py --config $config_value --desde $desde --hasta $hasta --debug"
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