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
MQTT_BROKER = "192.168.68.1XX"
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
last_sensor_read = 0.0
last_mqtt_send = 0.0
last_porta_state = None

def get_cpu_temp():
    try:
        res = os.popen('vcgencmd measure_temp').readline()
        return float(res.replace("temp=","").replace("'C\n",""))
    except: return None

def send_discovery():
    sensors = [
        ("temp_box", "Temp Box", "°C"), ("umid_box", "Umidade Box", "%"),
        ("temp_eletronica", "Temp Eletronica", "°C"), ("temp_cpu", "Temp CPU", "°C"),
        ("porta", "Status Porta", " "), ("cpu", "Uso CPU", "%"),
        ("ram_percent", "Porcentagem RAM", "%"), ("ram_used", "RAM Usada", "MB"),
        ("ram_total", "RAM Total", "MB")
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
            "device": {"identifiers": ["box_raspberry"], "name": "Controle Box"}
        }
        client.publish(topic, json.dumps(payload), retain=True)

    # SWITCH LED
    client.publish("homeassistant/switch/box_led/config", json.dumps({
        "name": "LED Box",
        "command_topic": "printer/box/cmd/led",
        "state_topic": "printer/box/state/led",
        "unique_id": "box_led",
        "payload_on": "ON",
        "payload_off": "OFF",
        "device": {"identifiers": ["box_raspberry"], "name": "Controle Box"}
    }), retain=True)

    # SWITCH COOLER
    client.publish("homeassistant/switch/box_cooler/config", json.dumps({
        "name": "Cooler Box Manual",
        "command_topic": "printer/box/cmd/cooler",
        "state_topic": "printer/box/state/cooler",
        "unique_id": "box_cooler",
        "payload_on": "ON",
        "payload_off": "OFF",
        "device": {"identifiers": ["box_raspberry"], "name": "Controle Box"}
    }), retain=True)

# ========================
# MQTT
# ========================
if USE_MQTT:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    client.username_pw_set("HA", "@nN&XXX")

    def on_message(client, userdata, msg):
        global comando_HA_led, comando_HA_cooler, cooler_box_on
        comando = msg.payload.decode()
        if msg.topic == "printer/box/cmd/led":
            comando_HA_led = (comando == "ON")
            GPIO.output(PIN_LED_FITA, comando_HA_led)
        elif msg.topic == "printer/box/cmd/cooler":
            comando_HA_cooler = (comando == "ON")
            cooler_box_on = comando_HA_cooler
            GPIO.output(PIN_COOLER_BOX, cooler_box_on)

    client.on_message = on_message
    
    try:
        client.connect(MQTT_BROKER, 1883, 60)
        client.loop_start()
        client.subscribe("printer/box/cmd/#")
        send_discovery()
    except Exception as e: print(f"Erro MQTT: {e}")

# ========================
# LOOP PRINCIPAL
# ========================
try:
    while True:
        agora = time.time()
        forçar_envio = False

        # 1. LEITURA SENSOR PORTA (Instantânea)
        porta_aberta = GPIO.input(PIN_SENSOR_PORTA) == 0
        if porta_aberta != last_porta_state:
            last_porta_state = porta_aberta
            forçar_envio = True
            print(f"DEBUG: Mudança de porta para {porta_aberta}")
        
        GPIO.output(PIN_LED_FITA, True if porta_aberta else comando_HA_led)

        # 2. LEITURA SENSORES (A cada 10s)
        if agora - last_sensor_read >= 10.0:
            last_sensor_read = agora
            try: 
                last_temp_box = dht_box.temperature
                last_umid_box = dht_box.humidity
                last_temp_eletronica = dht_eletronica.temperature
            except: pass

        # 3. LÓGICA COOLER ELETRÔNICA (Digital / Liga-Desliga)
        if last_temp_eletronica is not None:
            status_cooler = last_temp_eletronica > 29
            GPIO.output(PIN_COOLER_ELETRONICA, status_cooler)
        else:
            GPIO.output(PIN_COOLER_ELETRONICA, False)

        # 4. LÓGICA COOLER BOX
        if not comando_HA_cooler and last_temp_box is not None:
            cooler_box_on = (last_temp_box > 35)
            GPIO.output(PIN_COOLER_BOX, cooler_box_on)

        # 5. MQTT ENVIO
        if USE_MQTT and ((agora - last_mqtt_send >= 10.0) or forçar_envio):
            last_mqtt_send = agora
            try:
                payload = {
                    "temp_box": last_temp_box, "umid_box": last_umid_box, 
                    "temp_eletronica": last_temp_eletronica, "temp_cpu": get_cpu_temp(),
                    "cooler_box": cooler_box_on,
                    "porta": "Aberta" if porta_aberta else "Fechada"
                }

                client.publish(MQTT_TOPIC, json.dumps(payload), qos=1)

                client.publish(
                    "printer/box/state/led",
                    "ON" if comando_HA_led or porta_aberta else "OFF",
                    retain=True
                    )
            except: pass

        time.sleep(0.5)
except KeyboardInterrupt:
    print("Encerrando...")
finally:
    GPIO.cleanup()
