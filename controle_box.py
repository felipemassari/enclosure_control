import time
import board
import adafruit_dht
import RPi.GPIO as GPIO
import paho.mqtt.client as mqtt
import json
import psutil

# CONFIG MQTT
# ========================
MQTT_BROKER = "192.168.68." # IP do HA
MQTT_TOPIC = "printer/box"

USE_MQTT = True  # se quiser desligar facil

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

# SENSORES DHT
# ========================
dht_eletronica = adafruit_dht.DHT11(board.D4)
dht_box = adafruit_dht.DHT11(board.D17)

# VAR
# ========================
cooler_box_on = False
modo_manual = False
modo_manual_led = False
last_temp_box = None
last_temp_eletronica = None
last_umid_box = None
tempo_manual = time.time()
tempo_manual_led = time.time()
last_valid_time = time.time()

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

    # SWITCH LED
    client.publish("homeassistant/switch/box_led/config", json.dumps({
        "name": "LED Box",
        "command_topic": "printer/box/cmd/led",
        "unique_id": "box_led",
        "payload_on": "ON",
        "payload_off": "OFF",
        "device": {
            "identifiers": ["box_raspberry"],
            "name": "Controle Box"
        }
    }), retain=True)

    # SWITCH COOLER
    client.publish("homeassistant/switch/box_cooler/config", json.dumps({
        "name": "Cooler Box Manual",
        "command_topic": "printer/box/cmd/cooler",
        "state_topic": "printer/box/state/cooler",
        "unique_id": "box_cooler",
        "payload_on": "ON",
        "payload_off": "OFF",
        "device": {
            "identifiers": ["box_raspberry"],
            "name": "Controle Box"
        }
    }), retain=True)

if USE_MQTT:
    client = mqtt.Client()
    client.username_pw_set("HAA", "@nN&casa") # usuario e senha, ajustar
    client.connect(MQTT_BROKER, 1883, 60)

    def on_message(client, userdata, msg):
        global cooler_box_on, modo_manual, modo_manual_led, tempo_manual, tempo_manual_led
        comando = msg.payload.decode()

        if msg.topic == "printer/box/cmd/led":
            comando = msg.payload.decode()
            GPIO.output(PIN_LED_FITA, comando == "ON")
            modo_manual_led = True
            tempo_manual_led = time.time()
            print("MSG RECEBIDA:led")

        elif msg.topic == "printer/box/cmd/cooler":
            comando = msg.payload.decode()
            GPIO.output(PIN_COOLER_BOX, comando == "ON")
            cooler_box_on = (comando == "ON")
            modo_manual = True
            tempo_manual = time.time()
            print("MSG RECEBIDA:cooler")
            
        print("MSG RECEBIDA:", msg.topic, msg.payload)

    client.on_message = on_message
    client.subscribe("printer/box/cmd/#")

    client.loop_start()
    send_discovery()

# LOOP
try:
    while True:
            
        # LED HEARTBEAT
        # ========================
        GPIO.output(PIN_LED_DATA, True)
        time.sleep(2)
        GPIO.output(PIN_LED_DATA, False)
        time.sleep(1)
        GPIO.output(PIN_LED_DATA, True)
        time.sleep(2)
        GPIO.output(PIN_LED_DATA, False)
        
        # Leitura processador Raspberry 
        # ========================       
        temp_cpu = open("/sys/class/thermal/thermal_zone0/temp").read()
        temp_cpu = round(float(temp_cpu) / 1000,1)
        # Uso de CPU
        cpu_percent = psutil.cpu_percent(interval=1)
        # Uso de RAM
        ram = psutil.virtual_memory()
        ram_percent = ram.percent
        ram_used = round(ram.used / (1024**2), 1)   # MB
        ram_total = round(ram.total / (1024**2), 1)

        # leitura sensores
        try:
            temp_eletronica = dht_eletronica.temperature
            temp_box = dht_box.temperature
            umid_box = dht_box.humidity
        except RuntimeError:
            temp_eletronica = None
            temp_box = None
            umid_box = None
        
            # salva ultimo valor valido evitando NONE
        if temp_box is not None:
                last_temp_box = temp_box
                last_valid_time = time.time()
                
        if umid_box is not None:
                last_umid_box = umid_box

        if temp_eletronica is not None:
                last_temp_eletronica = temp_eletronica

        # Usa ultimo valor valido
        temp_box = last_temp_box
        umid_box = last_umid_box
        temp_eletronica = last_temp_eletronica
        
        if time.time() - last_valid_time > 300:
                print("SENSOR BOX SEM LEITURA HA 5 MINUTOS!")

        # COOLER BOX (HISTERESE)        
        # ========================
        if not modo_manual:
            if temp_box is not None:
                if not cooler_box_on and temp_box > 35:
                    GPIO.output(PIN_COOLER_BOX, True)
                    cooler_box_on = True
                elif cooler_box_on and temp_box < 33:
                    GPIO.output(PIN_COOLER_BOX, False)
                    cooler_box_on = False

        # Desligar cooler modo manual depois de 5 minutos
        if modo_manual and (time.time() - tempo_manual > 300):
            modo_manual = False
        
        # Desligar fita de led modo manual depois de 5 minutos
        if modo_manual_led and (time.time() - tempo_manual_led > 300):
                modo_manual_led = False
        
        # porta
        porta_aberta = GPIO.input(PIN_SENSOR_PORTA) == 0

        # LED automatico (etapa 3)
        if not modo_manual_led:
            if porta_aberta:
                GPIO.output(PIN_LED_FITA, True)
            else:
                GPIO.output(PIN_LED_FITA, False)

        # PWM ELETRONICA
        # ========================
        duty = 0
        if temp_eletronica is not None:
            if temp_eletronica < 28:
                duty = 0
            elif temp_eletronica < 35:
                duty = 10
            elif temp_eletronica < 45:
                duty = 25
            else:
                duty = 50

        pwm.ChangeDutyCycle(duty)

        # MQTT
        # ========================
        if USE_MQTT:
            try:
                payload = {
                    "temp_box": temp_box,
                    "umid_box": umid_box,
                    "temp_eletronica": temp_eletronica,
                    "temp_cpu": temp_cpu,
                    "cooler_box": cooler_box_on,
                    "cooler_pwm": duty,
                    "porta": porta_aberta,
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


        # DEBUG
        # ========================
        print("==== STATUS ====")
        print(f"Temp Box: {temp_box} °C")
        print(f"Umidade Box: {umid_box} %")
        print(f"Temp Eletronica: {temp_eletronica} °C")
        print(f"Temp CPU: {temp_cpu} °C")
        print(f"Cooler Box: {cooler_box_on}")
        print(f"PWM: {duty}%")
        print(f"Porta: {porta_aberta}")
        print(f"CPU: {cpu_percent}% | RAM: {ram_percent}%")
        print("================\n")

        time.sleep(10)

except KeyboardInterrupt:
    print("Encerrando...")

finally:
    pwm.stop()
    GPIO.cleanup()
