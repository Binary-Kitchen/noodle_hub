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
standby_timers = dict()
light_state = False


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

def init_gpios():
        lines["lights"] = init_line(config['lights-gpio'])
        for printer in config['printers']:
                printer_name = printer['name']
                lines['{}_{}'.format(printer_name,"rpi")] = init_line(printer["raspi-gpio"])
                lines['{}_{}'.format(printer_name,"pwr")] = init_line(printer["power-gpio"])

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

        for printer in config['printers']:
                printer_name = printer['name']
                printer_mqtt_name = printer['mqtt-name']
                printer_connection_status[printer_name] = False
                if 'mqtt-prefix' in printer:
                        prefix = printer['mqtt-prefix']
                else:
                        prefix = config['mqtt-prefix']

                connected_topic = prefix+printer['mqtt-connected-topic']
                print_progress_topic = prefix+printer['mqtt-print-progress-topic']
                power_command_topic = prefix + printer_mqtt_name + "/power/cmd"
                rpi_command_topic = prefix + printer_mqtt_name + "/rpi/cmd"

                client.subscribe([ ( connected_topic, 0),
                                   ( print_progress_topic, 0), 
                                   ( power_command_topic, 0),
                                   ( rpi_command_topic, 0) ])

                log.debug("Connected topic for printer {}: {}".format(printer_name, connected_topic))
                log.debug("Print progress topic for printer {}: {}".format(printer_name, print_progress_topic))
                log.debug("Power cmd topic for printer {}: {}".format(printer_name, power_command_topic))
                log.debug("RPI cmd topic for printer {}: {}".format(printer_name, rpi_command_topic))

                client.message_callback_add(connected_topic, 
                                            lambda client, userdata, msg : 
                                                mqtt_on_connected(client, userdata, msg, printer))
                client.message_callback_add(print_progress_topic, 
                                            lambda client, userdata, msg : 
                                                mqtt_on_print_progress(client, userdata, msg, printer))
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

        lines["{}_rpi".format(name)].set_value(state)
        lines["{}_pwr".format(name)].set_value(state)

        prefix = config['mqtt-prefix']
        mqtt_name = printer['mqtt-name']
        client.publish(prefix + mqtt_name + '/power/state', state)
        client.publish(prefix + mqtt_name + '/rpi/state', state)

        return printer_state_change_response(True)
        
def lights_cmd(state):
        lines["lights"].set_value(state)
        prefix = config['mqtt-prefix']
        client.publish(prefix + 'lights/state',str(int(state)))

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

def mqtt_on_print_progress(client, userdata, msg, printer):
        log.info("Received print progress message for printer {}: {}".format(printer['name'], msg.payload))
        printer_name = printer["name"]
        data = json.loads(msg.payload)
        flags = data["printer_data"]["state"]["flags"]
        if not flags["operational"] or flags["finishing"]:
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
        state = int(msg.payload)
        lights_cmd(state)

def mqtt_on_rpi_cmd(client, userdata, msg, printer):
        state = int(msg.payload)
        printer_change_state(state,printer)

def mqtt_on_power_cmd(client, userdata, msg, printer):
        state = int(msg.payload)
        printer_change_state(state,printer)

@app.route('/',methods=['GET', 'POST'])
def web_main():
        res = None
        if request.method == 'POST':
                printer = request.form.get('printer')
                printer_config = get_printer_from_config(config, printer)
                cmd = request.form.get('cmd')
                if cmd == "lights":
                        value = False if request.form.get('value')=="0" else True
                        log.info("change light state to {}".format(value))
                        lights_cmd(value)
                else:
                        if cmd == 'power_off':
                                desired_state = False
                                log.info("Received shutdown  command  for printer {}".format(printer))
                        elif cmd == 'power_on':
                                desired_state = True
                                log.info("Received power on command for printer {}".format(printer))
                        else:
                                abort(400)
                        res = printer_change_state(desired_state, printer_config)

        return render_template("index.html", printers=config['printers'],
                                             light_state=light_state,
                                             info=info, 
                                             online_status=printer_connection_status,
                                             result = res)



if __name__ == "__main__":
        config = read_config()
        logging.basicConfig(level=log_level, stream=sys.stdout,
                        format=log_fmt, datefmt=date_fmt)
        init_gpios()
        client = mqtt.Client()
        client.connect(config['mqtt-host'], config['mqtt-port'], 60)
        threading.Thread(target=mqtt_worker).start()
        app.run(host="0.0.0.0",debug=True)
