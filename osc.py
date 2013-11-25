# An OSC-based interface for controlling pyprocgame with OSC devices
# for pyprocgame, a Python-based pinball software development framework
# for use with P-ROC written by Adam Preble and Gerry Stellenberg
# More information is avaible at http://pyprocgame.pindev.org/
# and http://pinballcontrollers.com/

# More info on OSC at http://opensoundcontrol.org/
# This OSC interface was written by Brian Madden
# Version 0.2 - Nov 24, 2013

# This code is released under the MIT License.

#The MIT License (MIT)

#Copyright (c) 2013 Brian Madden

#Permission is hereby granted, free of charge, to any person obtaining a copy
#of this software and associated documentation files (the "Software"), to deal
#in the Software without restriction, including without limitation the rights
#to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#copies of the Software, and to permit persons to whom the Software is
#furnished to do so, subject to the following conditions:

#The above copyright notice and this permission notice shall be included in
#all copies or substantial portions of the Software.

#THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#THE SOFTWARE.


# This code requires pyOSC, https://trac.v2.nl/wiki/pyOSC
# It was written for pyOSC 0.3.5b build 5394,
# though I would expect later versions should work

# It also requires that the "desktop" mode is enabled in pyprocgame,
# and it requires some changes to procgame/game/gaem.py
# See http://www.pinballcontrollers.com/forum for more information

from ..game import Mode
import OSC
import socket
import threading
import pinproc
import time
import procgame


class OSC_Mode(Mode):
    """This is the awesome OSC interface.
    
    Parameters:
    game -- game object
    priority -- game mode priority. It doesn't really matter for this mode.
    serverIP -- the IP address the OSC server will listen on. If you don't pass
    it anything it will use the default IP address of your computer which
    should be fine
    serverPort -- the UDP port the server will listen on. Default 9000
    clientIP -- the IP address of the client you'd like to connect to.
    Leave it blank and it will automatically connect to the first client that
    contacts it
    clientPort -- the client UDP port. Default is 8000
    closed_switches -- a list of switch names that you'd like to have set "closed"
    by default, which is food for troughs and stuff. There's logic here that these
    default switches are only set to closed with when fakepinproc is used.
    """
    def __init__(self, game, priority, serverIP=None, serverPort=9000,
                 clientIP=None, clientPort=8000, closed_switches=[]):
        super(OSC_Mode, self).__init__(game, priority)
        self.serverPort = serverPort
        self.clientPort = clientPort
        self.closed_switches = closed_switches
        if not serverIP:
            self.serverIP = socket.gethostbyname(socket.gethostname())
        else:
            self.serverIP = serverIP
        self.clientIP = clientIP
        self.client_needs_sync = False
        self.do_we_have_a_client = False
        self.client_last_update_time = None
        self.last_loop_time = 1

    def mode_started(self):
        """Starts the OSC server when this OSC game mode is loaded"""
        receive_address = (self.serverIP, self.serverPort)
        self.server = OSC.OSCServer(receive_address)
        self.server.addDefaultHandlers()
        self.server.addMsgHandler("default", self.process_message)

        # start the OSC server
        self.game.logger.info("OSC Server listening on %s:%s", self.serverIP,
                              self.serverPort)
        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.start()
        self.set_initial_closed_switches()

    def mode_stopped(self):
            self.OSC_shutdown()

    def OSC_shutdown(self):
        """Shuts down the OSC Server thread. If you don't do this python will
        hang when you exit the game."""
        self.server.close()
        self.game.logger.info("Waiting for the OSC Server thread to finish")
        self.server_thread.join()
        self.game.logger.info("OSC Server thread is done.")

    def process_message(self, addr, tags, data, client_address):
        """Receives OSC messages and acts on them"""

        # separate the incoming message into category and name parts
        # for example "/sw/rollover1" is split into "sw" and "rollover1"
        cat = (addr.split("/"))[1]  # it's 1 not 0 since it begins with a delimiter
        
        if cat == "refresh":  # client switched pages, mark for sync and return
            self.client_needs_sync = True
            return
        
        name = addr.split("/")[2]
        
        # since we just got a message from a client, let's set up a connection to it
        if not self.do_we_have_a_client:
            if not self.clientIP:  # if a client IP wasn't specified, use the one that just communicated with us now
                self.clientIP = client_address[0]
            self.clientTuple = (self.clientIP, self.clientPort)
            self.setup_OSC_client(self.clientTuple)

        if cat == "sw":
            self.process_switch(name, data)

        elif cat == "lamp":
            self.process_lamp(name, data)

        elif cat == "led" or cat == "LED":
            self.process_LED(name, data)
            
        elif cat == "coil":
            self.process_coil(name, data)

    def process_switch(self, switchname, data):
        """Processes a switch event received from the OSC client"""

        if switchname in self.game.switches:
            switch_number = self.game.switches[switchname].number
        else:
            switch_number = pinproc.decode(self.game.machine_type, switchname)

        # I'm kind of cheating by using desktop.key_events here, but I guess this is ok?
        if data[0] == 1.0:  # close the switch
            self.game.desktop.key_events.append(
                {'type': pinproc.EventTypeSwitchClosedDebounced,
                 'value': switch_number})
        elif data[0] == 0.0:  # open the switch
            self.game.desktop.key_events.append(
                {'type': pinproc.EventTypeSwitchOpenDebounced,
                 'value': switch_number})

    def process_lamp(self, lampname, data):
        """Processes a lamp event received from the OSC client.
        Note this applies to anything connected to a PD-8x8 or PD-16,
        including LEDs if you have them connected to those boards.
        """
        if lampname in self.game.lamps:
            if data[0] >= 1:
                self.game.lamps[lampname].enable()
            elif data[0] == 0:
                self.game.lamps[lampname].disable()
            else:
                self.game.lamps[lampname].schedule(self.convertToMask(data[0]), 0, now=True)
        else:
            self.game.logger.warning("Received OSC command for lamp %s, but that lamp was not found. Ignoring.", lampname)

    def process_LED(self, LED, data):
        """Processes an LED event received from the OSC client.
        Note this applies only to LEDs connected to a PD-LED board.
        It requires the pyprocgame PD-LED code from here:
        http://www.pinballcontrollers.com/forum/index.php?topic=982.0
        """
        
        # create a dictionary value from the data in the OSC message
        brightness = []
        brightness.append(int(data[0]*255))
        
        # if the LED name starts with '+', it's a LED board address, dash, LED output
        # for example /led/#8-60 is LED output 60 on PD-LED board at address 8
        if LED.startswith("+"):
            LEDpart = LED
            LEDpart = LEDpart.strip("+")  # strip off the hash
            LEDpart = LEDpart.split('-')  # split the LED into board address and LED output
            brightness = int(data[0]*255)
            self.game.proc.PRLED_color(int(LEDpart[0]), int(LEDpart[1]), brightness)

        else:  # assume we got a LED name
            self.game.leds[LED].color(brightness)

        # send a message back to the OSC client to update any labels for this LED
        self.client_send_OSC_message("led-label", str(LED), brightness)
 
    def process_coil(self, coilname, data):
        """Processes a coil event received from the OSC client."""
        if coilname in self.game.coils:
            self.game.coils[coilname].pulse()

    def convertToMask(number):
        """Converts an int to a 32-bit PWM mask.
        More details here:
        http://www.pinballcontrollers.com/forum/index.php?topic=981
        """
        whole_num = 0  # tracks our whole number
        schedule = 0  # our output 32-bit mask
        count = 0  # our current count

        for _i in range(32):
            count += number
            if int(count) > whole_num:
                schedule = schedule | 1
                whole_num += 1
            schedule = schedule << 1
        return schedule

    def client_update_all(self):
        """ Update the OSC client.
        Good for when it switches to a new tab or connects a new client
        """
        self.client_update_all_switches()
        self.client_needs_sync = False  # since the sync is done we reset the flag

    def client_update_all_switches(self):
        """ Updates all the switch states on the OSC client."""
        for switch in self.game.switches:
            data = 0  # set the status to 'off'
            if switch.state:
                data = 1  # if the switch.state is 'True', the switch is closed

            self.client_send_OSC_message("sw", switch.name, data)

    def client_send_OSC_message(self, category, name, data):
        """Sendz an OSC message to the client to update it
        Parameters:
        category - type of update, sw, coil, lamp, led, etc.
        name - the name of the object we're updating
        data - the data we're sending
        """

        self.OSC_message = OSC.OSCMessage("/" + str(category) + "/" + name)
        self.OSC_message.append(data)
        self.game.logger.debug("OSC Message: %s", self.OSC_message)
        self.OSC_client.send(self.OSC_message)

    def setup_OSC_client(self, address):
        """Setup a new OSC client"""
        self.OSC_client = OSC.OSCClient()
        self.OSC_client.connect(address)
        self.do_we_have_a_client = True

    def set_initial_closed_switches(self):
        """If FakePinProc is being used, sets up the initial switches that should be closed, then marks the client to sync
        """

        if procgame.config.values['pinproc_class'] == 'procgame.fakepinproc.FakePinPROC':
            for switchname in self.closed_switches:  # run through the list of closed_switches passed to the mode as args
                if switchname in self.game.switches:  # convert the names to switch numbers
                    switch_number = self.game.switches[switchname].number
                else:
                    switch_number = pinproc.decode(self.game.machine_type,
                                                   switchname)
                self.game.desktop.key_events.append({
                    'type': pinproc.EventTypeSwitchClosedDebounced,
                    'value': switch_number})  # add these switch close events to the queue

            self.client_needs_sync = True  # Now that this is done we set the flag to sync the client
            # we use the flag because if we just did it now it's too fast
            # since the game loop hasn't read in the new closures yet

    def mode_tick(self):
        """Updates the OSC client with anything changed since the last loop"""
        if self.do_we_have_a_client:  # only proceed if we've establish a connection with a client
            if self.client_needs_sync:  # if the client is out of sync, then sync it
                self.client_update_all()

            for switch in self.game.switches:
                if switch.last_changed:  # This is 'None' if the switch has never been changed
                    if switch.last_changed > self.last_loop_time:
                        data = 0
                        if switch.state:
                            data = 1
                        self.client_send_OSC_message("sw", switch.name, data)

            self.last_loop_time = time.time()
