#!/usr/bin/env python

""" 
Noodle Hub
Copyright (c) Thomas Schmid, 2019
Author:
  Thomas Schmid <tom@binary-kitchen.de>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import logging
import sys
import yaml
import paho.mqtt.client as mqtt
import threading
import json

import gpiod
from flask import Flask, render_template, request, redirect, abort

info = dict()
info["author"] = 'Thomas Schmid'
info["copyright"] = 'Copyright (c) Thomas Schmid, 2019'
info["license"] = 'GPLv3'
info["email"] = 'tom@binary-kitchen.de'

app = Flask(__name__)
config = dict()

log_level = logging.DEBUG
date_fmt = '%Y-%m-%d %H:%M:%S'
log_fmt = '%(asctime)-15s %(levelname)-8s %(message)s'
log = logging.getLogger()

printer_connection_status = dict()
printer_idle_status = dict()
printer_gpio_status = dict()
standby_timers = dict()


chip = gpiod.Chip("gpiochip0")
lines = dict()

client = mqtt.Client()

prog = sys.argv[0]

def read_config(): 
    with open('config.yaml') as f:
        return yaml.load(f,yaml.FullLoader)

def init_line(number):
        line = chip.get_line(number)
        line.request(consumer=prog,type = gpiod.LINE_REQ_DIR_OUT)
        return line

def is_line_initialized(line):
    if line.is_requested() and line.direction() == gpiod.Line.DIRECTION_OUTPUT:
        return True
    return False

def init_gpios():
        if lines.get('lights') is None or is_line_initialized(lines["lights"]):
            lines["lights"] = init_line(config['lights-gpio'])

        default_state = config.get("default-light-state")
        if default_state is not None:
            lines["lights"].set_value(not default_state)
        else:
            lines["lights"].set_value(1)

        for printer in config['printers']:
                printer_name = printer['name']
                rpi_name = '{}_{}'.format(printer_name,'rpi')
                pwr_name = '{}_{}'.format(printer_name,'pwr')
                if lines.get(rpi_name) is None or is_line_initialized(lines[rpi_name]):
                    lines[rpi_name] = init_line(printer["raspi-gpio"])
                if lines.get(pwr_name) is None or is_line_initialized(lines[pwr_name]):
                    lines[pwr_name] = init_line(printer["power-gpio"])

                default_state = printer.get("default-power-state")
                if default_state is not None:
                    lines[rpi_name].set_value(not default_state)
                    lines[pwr_name].set_value(not default_state)

                printer_gpio_status[rpi_name] = not lines[rpi_name].get_value()
                printer_gpio_status[pwr_name] = not lines[pwr_name].get_value()
        log.info(lines)


def get_printer_from_config(config,printer_name):
        for p in config['printers']:
                if p['name'] == printer_name:
                        return p
        return None

def mqtt_worker():
        prefix = config['mqtt-prefix']
        lights_topic = prefix + "lights/cmd"
        client.subscribe(lights_topic,0)
        client.message_callback_add(lights_topic, mqtt_on_lights_cmd)
        log.debug("Lights state topic: {}".format(prefix + "lights/state"))
        log.debug("Lights cmd topic: {}".format(lights_topic))

        for printer in config['printers']:
                printer_name = printer['name']
                printer_mqtt_name = printer['mqtt-name']
                printer_connection_status[printer_name] = False
                if 'mqtt-prefix' in printer:
                        prefix = printer['mqtt-prefix']
                else:
                        prefix = config['mqtt-prefix']

                connected_topic = prefix+printer['mqtt-connected-topic']
                printer_state_change_topic = prefix+printer['mqtt-print-progress-topic']
                power_command_topic = prefix + printer_mqtt_name + "/power/cmd"
                rpi_command_topic = prefix + printer_mqtt_name + "/rpi/cmd"

                client.subscribe([ ( connected_topic, 0),
                                   ( printer_state_change_topic, 0),
                                   ( power_command_topic, 0),
                                   ( rpi_command_topic, 0) ])

                log.debug("Connected topic for printer {}: {}".format(printer_name, connected_topic))
                log.debug("Print state change topic for printer {}: {}".format(printer_name, printer_state_change_topic))
                log.debug("Power cmd topic for printer {}: {}".format(printer_name, power_command_topic))
                log.debug("RPI cmd topic for printer {}: {}".format(printer_name, rpi_command_topic))

                client.message_callback_add(connected_topic, 
                                            lambda client, userdata, msg : 
                                                mqtt_on_connected(client, userdata, msg, printer))
                client.message_callback_add(printer_state_change_topic,
                                            lambda client, userdata, msg : 
                                                mqtt_on_printer_state_changed(client, userdata, msg, printer))
                client.message_callback_add(power_command_topic,
                                            lambda client, userdata, msg :
                                                mqtt_on_power_cmd(client, userdata, msg, printer))
                client.message_callback_add(rpi_command_topic,
                                            lambda client, userdata, msg :
                                                mqtt_on_rpi_cmd(client, userdata, msg, printer))

        client.loop_forever()



class printer_state_change_response:
        def __init__(self, success, msg=''):
                self.success = success
                self.msg = msg

def printer_change_state(state,printer):
        name = printer['name']
        log.info("change state of {} to {}".format(printer['name'],state))
        if not state and not printer_idle_status[printer['name']]:
                return printer_state_change_response(False, "can not shutdown printer while it is printing")

        lines["{}_rpi".format(name)].set_value(not state)
        lines["{}_pwr".format(name)].set_value(not state)

        printer_gpio_status["{}_rpi".format(name)] = state
        printer_gpio_status["{}_pwr".format(name)] = state

        log.info(printer_gpio_status)

        prefix = config['mqtt-prefix']
        mqtt_name = printer['mqtt-name']
        client.publish(prefix + mqtt_name + '/power/state', state)
        client.publish(prefix + mqtt_name + '/rpi/state', state)

        return printer_state_change_response(True)
        
def lights_cmd(state):
        lines["lights"].set_value(not state)
        prefix = config['mqtt-prefix']
        client.publish(prefix + 'lights/state',str(int(state)))

def get_light_state():
        return lines["lights"].get_value()

def mqtt_on_connected(client, userdata, msg, printer):
        log.info("Received connection message for printer {}: {}".format(printer['name'], msg.payload))
        printer_connection_status[printer['name']] = msg.payload == b'connected'
        log.info(printer_connection_status)
        
        # if all printers are offline, turn of lights
        if all(v == False for v in printer_connection_status.values()):
                pass
                #lights_cmd(False)
        else:
                lights_cmd(True)

def mqtt_on_printer_state_changed(client, userdata, msg, printer):
        log.info("Received print state change message for printer {}: {}".format(printer['name'], msg.payload))
        printer_name = printer["name"]
        data = json.loads(msg.payload)
        state = data['state_id']
        if state == "OPERATIONAL" or state == "ERROR" and printer_connection_status[printer_name]:
                printer_idle_status[printer_name] = True
                standby_timeout = config["standby-timeout"]
                if printer_name in standby_timers:
                        standby_timers[printer_name].cancel()
                timer = threading.Timer(standby_timeout, lambda: printer_change_state(False,printer))
                standby_timers[printer_name] = timer
                timer.start()

        else:
                printer_idle_status[printer_name] = False
                if printer_name in standby_timers:
                        standby_timers[printer_name].cancel()


def mqtt_on_lights_cmd(client, userdata, msg):
        state = mqtt_parse_boolean(msg)
        if state is not None:
                log.info("mqtt change lights to {}".format(state))
                lights_cmd(state)

def mqtt_on_rpi_cmd(client, userdata, msg, printer):
        state = mqtt_parse_boolean(msg)
        if state is not None:
                printer_change_state(state,printer)

def mqtt_on_power_cmd(client, userdata, msg, printer):
        state = mqtt_parse_boolean(msg)
        if state is not None:
                printer_change_state(state,printer)

def mqtt_parse_boolean(msg):
        if msg.payload.decode() == "0":
                state = False
        elif msg.payload.decode() == "1":
                state = True
        else:
                log.info("received mqtt topic ""{}"" with invalid payload: {}".format(
                        msg.topic,
                        msg.payload
                ))
                return None
        return state

@app.route('/',methods=['GET', 'POST'])
def web_main():
        res = None
        if request.method == 'POST':
                printer = request.form.get('printer')
                printer_config = get_printer_from_config(config, printer)
                cmd = request.form.get('cmd')
                if cmd == "lights":
                        value = lines["lights"].get_value()
                        log.info("change light state to {}".format(value))
                        lights_cmd(value)
                else:
                        if cmd == 'power_off':
                                desired_state = False
                                log.info("Received shutdown command  for printer {}".format(printer))
                        elif cmd == 'power_on':
                                desired_state = True
                                log.info("Received power on command for printer {}".format(printer))
                        else:
                                abort(400)
                        res = printer_change_state(desired_state, printer_config)
                        log.info(res)
        return render_template("index.html", printers=config['printers'],
                                             light_state=get_light_state(),
                                             info=info,
                                             gpio_status=printer_gpio_status,
                                             online_status=printer_connection_status,
                                             idle_status=printer_idle_status,
                                             result = res)



if __name__ == "__main__":
        config = read_config()
        logging.basicConfig(level=log_level, stream=sys.stdout,
                        format=log_fmt, datefmt=date_fmt)

        init_gpios()


        #init printer idle and connection status
        for printer in config["printers"]:
                printer_idle_status[printer["name"]] = True
                printer_connection_status[printer["name"]] = False

        client = mqtt.Client()
        with open("credentials.yaml") as f:
            mqtt_auth = yaml.load(f,yaml.FullLoader)
        client.username_pw_set(mqtt_auth["username"], mqtt_auth["password"])
        client.connect(config['mqtt-host'], config['mqtt-port'], 60)

        threading.Thread(target=mqtt_worker).start()
        app.run(host="0.0.0.0",debug=True, use_reloader=False)
