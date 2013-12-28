#!/usr/bin/env python

from __future__ import print_function

import serial
import time
import sys
import os
import re
import random
import logging
import colorsys
import binascii
import threading
import xmlrpclib
import supervisor.xmlrpc

from supervisor import childutils
from threading import Thread
from multiprocessing import Queue


log = logging.getLogger("spuelmeister")


COLORS = {
    # switch event ON OFF -> start/stop send to supervisor
    "COMMAND_ISSUED" : "#000000",

    # switch event ON OFF -> start/stop send to supervisor but threw exception
    "COMMAND_FAILED" : "#010101",

    "STATE_CHANGE_UNKNOWN" : "#000000",

    # supervisord state changes
    # see: http://supervisord.org/subprocess.html#process-states

    "UNKNOWN->RUNNING" : "#000100",
    "UNKNOWN->STOPPED" : "#010000",
    "UNKNOWN->STARTING" : "#010100",
    "UNKNOWN->STOPPING" : "#010100",
    "UNKNOWN->BACKOFF" : "#010001",
    "UNKNOWN->FATAL" : "#010001",

    "STARTING->RUNNING" : "#000100",
    "STOPPING->STOPPED" : "#010000",

    "RUNNING->STOPPING" : "#010100",
    "STOPPED->STARTING" : "#010100",

    "STARTING->STOPPING" : "#010100",
    "STARTING->BACKOFF" : "#010001",

    "RUNNING->EXITED" : "#010100",

    "EXITED->STARTING" : "#010100",
    "FATAL->STARTING" : "#010100",

    "BACKOFF->FATAL" : "#010001",
    "BACKOFF->STARTING" : "#010001",
}

PROGRAMS = {
    "0" : "dnsmasq_131",
    "1" : "dnsmasq_132",
    "3" : "cat",
}

SWITCHES = { v : k for k, v in PROGRAMS.iteritems() }

class MrProperEvent(object):
    """
    Base class for mrproper events. Each subclass must specify a parse() classmethod and regex.
    A call to MrProperEvent.parse() returns an object of the class with the first matching regex
    """

    regex = r'^(SWITCH|BUTTON|STARTUP)'

    @classmethod
    def is_event(cls, mesg):
        if cls.regex != None:
            return re.match(cls.regex, mesg)

    @classmethod
    def parse(me, raw):
        if me.is_event(raw):
            for cls in me.__subclasses__():
                if cls.is_event(raw):
                    return cls.parse(raw)

    def execute(self, mrproper):
        pass

class StartupEvent(MrProperEvent):
    regex = re.compile(r'^STARTUP \w+')

    @classmethod
    def parse(cls, raw):
        e = StartupEvent()
        e.raw = raw
        e.name = raw.split()[1]
        return e

    def execute(self, ctx):
        log.info("Startup: Setting current program states")

        for p in ctx.supervisor.getAllProcessInfo():
            handle_process_state_change(ctx.mrproper, p["name"], "UNKNOWN", p["statename"])

class SwitchEvent(MrProperEvent):
    regex = re.compile(r'^SWITCH [0-9]+ (ON|OFF)')

    @classmethod
    def parse(cls, raw):
        t = raw.split()
        e = SwitchEvent()

        e.raw = raw
        e.switch = t[1]
        e.state = t[2]
        return e

    @classmethod
    def synthetic(cls, switch, state):
        e = SwitchEvent()

        e.raw = ""
        e.switch = switch
        e.state = state
        return e

    def execute(self, ctx):

        if not PROGRAMS.has_key(self.switch):
            log.error("No process mapped to switch %s", self.switch)
            return

        process = PROGRAMS[str(self.switch)]
        procinfo = None

        try:
            procinfo = ctx.supervisor.getProcessInfo(process)
            log.info("Changing state of \"%s\" (switch: %s): %s -> %s ",
                        process, self.switch, procinfo["statename"], self.state)

        except xmlrpclib.Fault, e:
            log.error("Can't get process state: %s", e)
            return

        try:
            if self.state == "ON":
                ctx.mrproper.set_led(self.switch, COLORS["COMMAND_ISSUED"])
                ctx.supervisor.startProcess(process, False)

            elif self.state == "OFF":
                ctx.mrproper.set_led(self.switch, COLORS["COMMAND_ISSUED"])
                ctx.supervisor.stopProcess(process, False)

            else:
                log.error("wut?")
        except xmlrpclib.Fault, e:
            log.error("Error while setting process state: %s", e)
            handle_process_state_change(ctx.mrproper, process, "UNKNOWN", procinfo["statename"])


class ButtonEvent(MrProperEvent):
    regex = re.compile(r'^BUTTON [0-9]+')

    @classmethod
    def parse(cls, raw):
        t = raw.split()
        e = ButtonEvent()

        e.raw = raw
        e.button = t[1]
        e.state = t[2]
        return e


class MrProper(Thread):
    REPLY_REGEX = r'^(LED|PONG|VERSION|SWITCHES)'

    # TODO add statement about thread safety

    def __init__(self, serial):
        Thread.__init__(self)
        self.daemon = True

        self.serial = serial
        self.eventq = Queue()
        self.replyq = Queue()

        self._send_lock = threading.RLock()

        self.startup()

    def reinitialize(self):
        try:
            for it in iter(self.eventq.get_nowait, None):
                log.warning("Reinit: Discarding event %s", it)
        except Exception:
            pass

        try:
            for it in iter(self.replyq.get_nowait, None):
                log.warning("Reinit: Discarding reply %s", it)
        except Exception:
            pass

        self.serial.flush()

    def run(self):
        self.loop()

    def process(self, mesg):
        """ Process incoming data into two categories/queues:
            - events
            - replies
        """
        log.debug("<--- Recvd something: \"%s\" [hex: \"%s\"]", mesg, binascii.hexlify(mesg))

        if re.match(self.REPLY_REGEX, mesg):
            log.debug("<--- Something is reply")
            self.replyq.put(mesg)

        elif MrProperEvent.is_event(mesg):
            log.debug("<--- Something is event")

            ev = MrProperEvent.parse(mesg)

            log.debug("EVENT: %s", ev)
            self.eventq.put(ev)
        else:
            log.error("<--- Received unsupported message: %s", mesg)

    def send(self, *args):
        """ Send command to mrproper. Convert args to string."""

        cmd = " ".join((str(x) for x in args))
        log.debug("---> Sending: \"%s\"", cmd)

        with self._send_lock:
            print(cmd, file=self.serial)

    def _recv_no_reset(self, regex, block=True, timeout=5):
        while True:
            item = None

            try:
                item = self.replyq.get(block, timeout)

            except Exception:
                log.error("Recv faild after %s seconds.", timeout)
                return None

            if re.match(regex, item):
                return item

            else:
                log.debug("recv: item \"\" didn't match regex \"\"", item, regex)
                self.replyq.put(item)

    def recv(self, regex, block=True, timeout=5):
        result = self._recv_no_reset(regex, block, timeout)
        if not result:
            self.reset()
            return ""

        return result

    def loop(self):
        while True:
            line = self.serial.readline() # blocks
            self.process(line)

    def set_led(self, led, color):
        self.send("LED", led, color)

    def ping_no_reset(self):
        self.send("PING")
        return self._recv_no_reset("^PONG", True, 4)

    def reset(self):
        log.warning("MrProper reset!")

        self.reinitialize()

        while not self.ping_no_reset():
            log.warning("Waiting for mrproper to respond")

        self.send("RESET")
        time.sleep(5)

        self.startup_animation()

    def startup(self):
        self.send("RESET")
        self.startup_animation()

    def startup_animation(self):
        anim = ["10000000",
                "11000000",
                "11100000",
                "01110000",
                "00111000",
                "00011100",
                "00001110",
                "00000111",
                "00000011",
                "00000001",
                "00000011",
                "00000111",
                "00001110",
                "00011100",
                "00111000",
                "01110000",
                "11100000",
                "11000000",
                "10000000" ]

        for ignore in range(0, 2):
            for frm in anim:
                for i, px in enumerate(frm):
                    self.set_led(i, ["#050000", "#000101"][int(px)])

        for color in ["#010101",
                      "#010100"]:

            for led in range(0, 8):
                self.set_led(led, color)
                time.sleep(0.1)

        time.sleep(1)

        for led in range(0, 8):
            self.set_led(led, "#000000")

    def ping(self, block=True, timeout=5):
        """special: wait only 5 seconds for PONG"""
        self.send("PING")
        res = self.recv("^PONG", block, timeout)

        return res != None

    def version(self):
        self.send("VERSION")
        res = self.recv("^VERSION").split()

        return res[1]

    def get_led(self, led):
        self.send("GET LED", led)
        res = self.recv("^LED %{}".format(led)).split()

        return res[2]

    def get_switches(self):
        self.send("GET SWITCHES")
        res = self.recv("^SWITCHES").split()

        sw = { i : val for i, val in (x.split(":") for x in res[1:]) }
        return sw

class EventProcessorContext(object):
    pass


class EventProcessor(Thread):
    def __init__(self, mrproper, supervisor):
        Thread.__init__(self)
        self.daemon = True
        self.mrproper = mrproper
        self.supervisor = supervisor

        self.make_context_object()

    def make_context_object(self):
        self.ctx = EventProcessorContext()
        self.ctx.mrproper = self.mrproper
        self.ctx.supervisor = self.supervisor

    def run(self):
        for ev in iter(self.mrproper.eventq.get, None):
            log.debug("Executing event: %s", ev)
            ev.execute(self.ctx)

def setup_logger():
    logf = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    logh = logging.StreamHandler()
    logh.setLevel(logging.DEBUG)
    logh.setFormatter(logf)

    log.addHandler(logh)
    log.setLevel(logging.DEBUG)


def random_html_color():
    return "#%02x%02x%02x" % tuple(map(lambda x : x*256, colorsys.hsv_to_rgb(random.random(), 1.0, 0.1)))


def connect_supervisor():
    sup = xmlrpclib.ServerProxy('http://127.0.0.1',
                                transport=supervisor.xmlrpc.SupervisorTransport(
                                    None, None, 'unix:////srv/spuelmeister/supervisor/supervisor.sock' ))

    return sup.supervisor


def check_supervisor_rpc_con(sup):
    assert sup.getState()["statename"] == "RUNNING"

    all_proc = [ d["name"] for d in sup.getAllProcessInfo()]
    # check supervisor processes with mrproper processes

    for p in PROGRAMS.itervalues():
        if not p in all_proc:
            log.error("Program \"%s\" unknown to supervisor", p)

def handle_process_state_change(mp, proc, frm, to):
    if not SWITCHES.has_key(proc):
        return

    sw = SWITCHES[proc]

    log.info("Process state change: Process \"%s\" (%s) from %s to %s", proc, sw, frm, to)

    color = "{}->{}".format(frm, to)

    if COLORS.has_key(color):
        mp.set_led(sw, COLORS[color])
    else:
        log.error("No color for state change %s defined", color)
        mp.set_led(sw, COLORS["STATE_CHANGE_UNKNOWN"])


def supervisor_event_listener(sup, mrproper):
    while True:
        headers, payload = childutils.listener.wait()

        if headers["eventname"].startswith("TICK"):
            if not mrproper.ping():
                mrproper.reset()

        elif headers["eventname"].startswith("PROCESS_STATE"):
            payload = { k : v for k, v in ( token.split(":") for token in payload.split())}

            frm = payload["from_state"]
            to = headers["eventname"][len("PROCESS_STATE_"):]

            process = payload["processname"]

            handle_process_state_change(mrproper, process, frm, to)

        childutils.listener.ok()

def main():
    setup_logger()

    s = serial.Serial("/dev/ttyACM0", 57600)

    sup = connect_supervisor()
    check_supervisor_rpc_con(sup)

    m = MrProper(s)
    m.start()

    eproc = EventProcessor(m, sup)
    eproc.start()

    supervisor_event_listener(sup, m)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("Bye!")
