import time
import board
import adafruit_dht
import RPi.GPIO as GPIO
import paho.mqtt.client as mqtt
import json
import psutil
import os

# ========================
# CONFIG MQTT
# ========================
MQTT_BROKER = "192.168.68.136"
MQTT_TOPIC = "printer/box"
USE_MQTT = True

# ========================
# GPIO
# ========================
GPIO.setmode(GPIO.BCM)

PIN_COOLER_BOX = 27
PIN_COOLER_ELETRONICA = 18
PIN_LED_DATA = 24
PIN_SENSOR_PORTA = 23
PIN_LED_FITA = 25

GPIO.setup(PIN_COOLER_BOX, GPIO.OUT)
GPIO.setup(PIN_COOLER_ELETRONICA, GPIO.OUT)
GPIO.setup(PIN_LED_DATA, GPIO.OUT)
GPIO.setup(PIN_LED_FITA, GPIO.OUT)
GPIO.setup(PIN_SENSOR_PORTA, GPIO.IN, pull_up_down=GPIO.PUD_UP)

pwm = GPIO.PWM(PIN_COOLER_ELETRONICA, 25000)
pwm.start(0)

# ========================
# SENSORES
# ========================
dht_eletronica = adafruit_dht.DHT11(board.D4)
dht_box = adafruit_dht.DHT11(board.D17)

# ========================
# VAR
# ========================
cooler_box_on = False
comando_HA_cooler = False
comando_HA_led = False

last_temp_box = None
last_temp_eletronica = None
last_umid_box = None

tempo_porta_fechada = None
tempo_temp_alta = None
cooler_timer = None

# Temporizadores de execução
last_sensor_read = 0.0   # Cronômetro para ler sensores lentos (10 segundos)
last_mqtt_send = 0.0     # Cronômetro para envio do MQTT (10 segundos)
last_porta_state = None  # Guarda o último estado da porta para detectar mudanças

# Variáveis do sistema persistentes
temp_cpu = 0.0
cpu_percent = 0.0
ram_percent = 0.0
ram_used = 0.0
ram_total = 0.0

# heartbeat
etapa = 0
last_time = time.time()

# Função para pegar a temperatura da CPU do Raspberry Pi
def get_cpu_temp():
    try:
        res = os.popen('vcgencmd measure_temp').readline()
        return float(res.replace("temp=","").replace("'C\n",""))
    except:
        return None

# ========================
# MQTT
# ========================
def send_discovery():
    sensors = [
        ("temp_box", "Temp Box", "°C"),
        ("umid_box", "Umidade Box", "%"),
        ("temp_eletronica", "Temp Eletronica", "°C"),
        ("temp_cpu", "Temp CPU", "°C"),
        ("porta", "Status Porta", " "),
        ("cpu", "Uso CPU", "%"),
        ("ram_percent", "Porcentagem RAM", "%"),
        ("ram_used", "RAM Usada", "MB"),
        ("ram_total", "RAM Total", "MB"),
    ]

    # SENSORES
    for key, name, unit in sensors:
        topic = f"homeassistant/sensor/box_{key}/config"
        payload = {
            "name": name,
            "state_topic": "printer/box",
            "value_template": f"{{{{ value_json.{key} }}}}",
            "unit_of_measurement": unit,
            "unique_id": f"box_{key}",
            "device": {
                "identifiers": ["box_raspberry"],
                "name": "Controle Box",
                "model": "Raspberry Pi",
                "manufacturer": "DIY"
            }
        }

        client.publish(topic, json.dumps(payload), retain=True)

if USE_MQTT:
    client = mqtt.Client()
    client.username_pw_set("HA", "cachorro")
    client.connect(MQTT_BROKER, 1883, 60)

    def on_message(client, userdata, msg):
        global comando_HA_led, comando_HA_cooler, cooler_box_on

        comando = msg.payload.decode()

        if msg.topic == "printer/box/cmd/led":
            if comando == "ON":
                comando_HA_led = True
                GPIO.output(PIN_LED_FITA, True)
            else:
                comando_HA_led = False
                GPIO.output(PIN_LED_FITA, False)

        elif msg.topic == "printer/box/cmd/cooler":
            if comando == "ON":
                comando_HA_cooler = True
                cooler_box_on = True
                GPIO.output(PIN_COOLER_BOX, True)
            else:
                comando_HA_cooler = False
                cooler_box_on = False
                GPIO.output(PIN_COOLER_BOX, False)

        print("MQTT:", msg.topic, comando)

    client.on_message = on_message
    client.subscribe("printer/box/cmd/#")
    client.loop_start()
    send_discovery()

# ========================
# LOOP
# ========================
try:
    while True:
        agora = time.time()
        forçar_envio_mqtt = False

        # ========================
        # HEARTBEAT (2 piscadas + pausa)
        # ========================
        if etapa == 0 and agora - last_time > 0:
            GPIO.output(PIN_LED_DATA, True)
            last_time = agora
            etapa = 1

        elif etapa == 1 and agora - last_time > 2:
            GPIO.output(PIN_LED_DATA, False)
            last_time = agora
            etapa = 2

        elif etapa == 2 and agora - last_time > 1:
            GPIO.output(PIN_LED_DATA, True)
            last_time = agora
            etapa = 3

        elif etapa == 3 and agora - last_time > 2:
            GPIO.output(PIN_LED_DATA, False)
            last_time = agora
            etapa = 4

        elif etapa == 4 and agora - last_time > 10:
            etapa = 0

        # ========================
        # PORTA + LED (PRINCIPAL - Executa a 0.1s para resposta física instantânea)
        # ========================
        porta_aberta = GPIO.input(PIN_SENSOR_PORTA) == 0

        # Se o estado da porta mudou fisicamente, força um envio de MQTT imediato
        if porta_aberta != last_porta_state:
            last_porta_state = porta_aberta
            forçar_envio_mqtt = True

        if porta_aberta:
            GPIO.output(PIN_LED_FITA, True)
            tempo_porta_fechada = None
        else:
            if tempo_porta_fechada is None:
                tempo_porta_fechada = time.time()

            if time.time() - tempo_porta_fechada > 5:
                if not comando_HA_led:
                    GPIO.output(PIN_LED_FITA, False)

        # ========================
        # LEITURA DE SENSORES TEMPORIZADA (Executa a cada 10 segundos)
        # ========================
        if agora - last_sensor_read >= 10.0 or last_temp_box is None:
            last_sensor_read = agora
            
            # 1. Ler DHT Box
            try:
                temp_box = dht_box.temperature
                umid_box = dht_box.humidity
            except:
                temp_box = last_temp_box
                umid_box = last_umid_box

            if temp_box is not None:
                last_temp_box = temp_box
            if umid_box is not None:
                last_umid_box = umid_box

            # 2. Ler DHT Eletrônica
            try:
                temp_eletronica = dht_eletronica.temperature
            except:
                temp_eletronica = last_temp_eletronica

            if temp_eletronica is not None:
                last_temp_eletronica = temp_eletronica

            # 3. Ler dados do Sistema (CPU e RAM)
            temp_cpu = get_cpu_temp()
            cpu_percent = psutil.cpu_percent()
            
            mem = psutil.virtual_memory()
            ram_percent = mem.percent
            ram_used = round(mem.used / (1024 * 1024), 1)
            ram_total = round(mem.total / (1024 * 1024), 1)

        # ========================
        # COOLER AUTO
        # ========================
        if not comando_HA_cooler and last_temp_box is not None:
            if not cooler_box_on and last_temp_box > 35:
                GPIO.output(PIN_COOLER_BOX, True)
                cooler_box_on = True
            elif cooler_box_on and last_temp_box < 33:
                GPIO.output(PIN_COOLER_BOX, False)
                cooler_box_on = False

        # ========================
        # PWM ELETRONICA
        # ========================
        # --- Lógica de Tempo Ajustada ---
        duty = 0

        if last_temp_eletronica is not None:
            if last_temp_eletronica < 28:
                duty = 0
                tempo_temp_alta = None  # Reseta o timer se esfriar antes de dar 2 min
            else:
                # Acima de 28, começa a contar o tempo
                if tempo_temp_alta is None:
                    tempo_temp_alta = agora

                # Se ficou quente continuamente por 2 minutos (120 segundos)
                if agora - tempo_temp_alta > 120:
                    cooler_timer = agora
                    tempo_temp_alta = None  # Reseta o gatilho para a próxima vez

        # Ciclo do cooler ativo por 3 minutos (180 segundos)
        if cooler_timer is not None:
            if agora - cooler_timer < 180:
                # Define a velocidade de acordo com a temperatura atual durante os 3 minutos
                if last_temp_eletronica is not None:
                    if last_temp_eletronica >= 45:
                        duty = 100
                    elif last_temp_eletronica >= 35:
                        duty = 60
                    else:
                        duty = 50
                else:
                    duty = 40
            else:
                # Desliga o cooler após os 3 minutos
                cooler_timer = None

        pwm.ChangeDutyCycle(duty)

        # ========================
        # MQTT (TEMPORIZADO A CADA 10 SEGUNDOS OU POR ALTERAÇÃO DE PORTA)
        # ========================
        if USE_MQTT:
            if (agora - last_mqtt_send >= 10.0) or forçar_envio_mqtt:
                last_mqtt_send = agora
                try:
                    payload = {
                        "temp_box": last_temp_box,
                        "umid_box": last_umid_box,
                        "temp_eletronica": last_temp_eletronica,
                        "temp_cpu": temp_cpu,
                        "cooler_box": cooler_box_on,
                        "cooler_pwm": duty,
                        "porta": "Aberta" if porta_aberta else "Fechada",
                        "cpu": cpu_percent,
                        "ram_percent": ram_percent,
                        "ram_used": ram_used,
                        "ram_total": ram_total
                    }
                    client.publish(MQTT_TOPIC, json.dumps(payload))
                    client.publish("printer/box/state/cooler",
                                   "ON" if cooler_box_on else "OFF",
                                   retain=True)
                except:
                    for _ in range(7):
                        GPIO.output(PIN_LED_DATA, True)
                        time.sleep(0.7)
                        GPIO.output(PIN_LED_DATA, False)
                        time.sleep(0.7)
                    print("Erro MQTT")

        time.sleep(0.1)

except KeyboardInterrupt:
    print("Encerrando...")

finally:
    pwm.stop()
    GPIO.cleanup()
