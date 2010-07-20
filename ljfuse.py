#!/usr/bin/env python

# ljfuse.py
# Copyright (c) 2010 LabJack Corporation <support@labjack.com>
# 
# Permission to use, copy, modify, and distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
# 
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.


from errno import ENOENT
from stat import S_IFDIR, S_IFREG
from time import time
import os, sys
from errno import EROFS, EACCES

from fuse import FUSE, Operations, LoggingMixIn, fuse_get_context

import LabJackPython, u3, u6, ue9, bridge

DEBUG = False

LJSOCKET_ADDRESS = "localhost"
LJSOCKET_PORT = "6000"

DEFAULT_MOUNT_POINT = "root-ljfuse"

MODBUS_ADDRS = {"0":0444, "2":0444, "4":0444, "6":0444,
                "5000":0664, "5002":0664, 
                "6000":0664, "6001":0664, "6002":0664, "6003":0664, 
                "6004":0664, "6005":0664, "6006":0664, "6007":0664,
                "6100":0664, "6101":0664, "6102":0664, "6103":0664, 
                "6104":0664, "6105":0664, "6106":0664, "6107":0664}

U3_LV_CONNECTION_LABELS = {"DAC0":(5000, 0664), "DAC1": (5002,0664)
                          }

U3_LV_FLEXIBLE_CONNECTION_LABELS = {
                     "FIO0":0, "FIO1":1, "FIO2":2, "FIO3":3, 
                     "FIO4":4, "FIO5":5, "FIO6":6, "FIO7":7
                    }

U3_HV_CONNECTION_LABELS = {"AIN0": (0, 0444), "AIN1":(2,0444), "AIN2":(4,0444), "AIN3":(6,0444),
                     "DAC0":(5000, 0664), "DAC1": (5002,0664)
                }

U3_HV_FLEXIBLE_CONNECTION_LABELS = {
                     "FIO4":4, "FIO5":5, "FIO6":6, "FIO7":7
                    }

U6_UE9_CONNECTION_LABELS = {"AIN0": (0, 0444), "AIN1":(2,0444), "AIN2":(4,0444), "AIN3":(6,0444),
                     "DAC0":(5000, 0664), "DAC1": (5002,0664),
                     "FIO0":(6000, 0664), "FIO1":(6001, 0664), "FIO2":(6002, 0664), "FIO3":(6003, 0664), 
                     "FIO4":(6004, 0664), "FIO5":(6005, 0664), "FIO6":(6006, 0664), "FIO7":(6007, 0664),    
                     "FIO0-dir":(6100, 0664), "FIO1-dir":(6101, 0664), "FIO2-dir":(6102, 0664), "FIO3-dir":(6103, 0664), 
                     "FIO4-dir":(6104, 0664), "FIO5-dir":(6105, 0664), "FIO6-dir":(6106, 0664), "FIO7-dir":(6107, 0664)
                }

class Path(object):
    def __init__(self, parent, myName):
        self.myName = myName
        self.children = []
        self.linkToParent(parent)

    def linkToParent(self, parent = None):
        if parent is not None:
            parent.children.append(self)

    def stripNullBytes(self, data):
        # If data contains a newline, get rid of everything after
        firstNewline = data.find('\n')
        if firstNewline != -1:
            data = data[:firstNewline]
        nonNullBytes = [b for b in data if b != '\x00']
        return ''.join(nonNullBytes)

class RootPath(Path):
    def __init__(self, myName = '/'):
        super(RootPath, self).__init__(None, myName)
        self.fileType = "DIR"

class DeviceNamePath(Path):
    def __init__(self, parent, myName):
        super(DeviceNamePath, self).__init__(parent, myName)
        self.fileType = "DIR"

class ModbusOpPath(Path):
    def __init__(self, parent, myName = "modbus"):
        super(ModbusOpPath, self).__init__(parent, myName)
        self.fileType = "DIR"

class ConnectionLabelOpPath(Path):
    def __init__(self, parent, myName = "connection"):
        super(ConnectionLabelOpPath, self).__init__(parent, myName)
        self.fileType = "DIR"

class ModbusAddrPath(Path):
    def __init__(self, parent, myName, device, addr, mode):
        super(ModbusAddrPath, self).__init__(parent, myName)
        self.fileType = "FILE"
        self.length = 6
        self.device = device
        self.addr = addr
        self.mode = mode

    def read(self):
        if DEBUG: print "ModbusAddrPath Reading addr " + str(self.addr)
        readResult = self.device.readRegister(self.addr)
        if type(readResult) == type(0.0):
            return '%0.3f\n' % readResult
        else:
            return str(readResult).ljust(self.length - 1) + '\n'

    def write(self, data):
        if DEBUG: print "ModbusAddrPath Writing addr " + str(self.addr) + " data = " + data
        if DEBUG: print "ModbusAddrPath len(data) = ", len(data)
        try:
            data = int(self.stripNullBytes(data))
        except ValueError:
            data = float(self.stripNullBytes(data))
        if DEBUG: print "ModbusAddrPath write data =", data
        self.device.writeRegister(self.addr, data)

class FlexibleIODirPath(Path):
    def __init__(self, parent, myName, device, ioNumber):
        super(FlexibleIODirPath, self).__init__(parent, myName)
        self.fileType = "FILE"
        self.length = 2
        self.mode = 0664
        self.device = device
        self.ioNumber = ioNumber
        analogInputs = self.device.configIO()['FIOAnalog']
        if (analogInputs >> self.ioNumber) & 1:
            self.state = 2
        else:
            bitDir, = self.device.getFeedback(u3.BitDirRead(self.ioNumber))
            self.state = bitDir
    
    def read(self):
        return "%d\n" % self.state

    def write(self, data):
        try:
            self.state = int(self.stripNullBytes(data))
        except ValueError:
            raise OSError(EACCES, 'Invalid value')
        if self.state == 2:
            self.device.configAnalog(self.ioNumber)
        else:
            self.device.configDigital(self.ioNumber)
            self.device.getFeedback(u3.BitDirWrite(self.ioNumber, self.state))

class FlexibleIOStatePath(Path):
    def __init__(self, parent, myName, device, ioNumber, flexibleIODirPath):
        super(FlexibleIOStatePath, self).__init__(parent, myName)
        self.fileType = "FILE"
        self.length = 6
        self.device = device
        self.ioNumber = ioNumber
        self.dirRef = flexibleIODirPath
        self.analogModbusAddr = 2 * ioNumber
        self.digitalModbusAddr = 6000 + ioNumber

    def read(self):
        if self.dirRef.state == 2:
            readResult = self.device.readRegister(self.analogModbusAddr)
            return '%0.3f\n' % readResult
        else:
            readResult = self.device.readRegister(self.digitalModbusAddr)
            return str(readResult).ljust(self.length - 1) + '\n'

    def write(self, data):
        if self.dirRef.state == 1:
            try:
                data = int(self.stripNullBytes(data))
            except ValueError:
                data = float(self.stripNullBytes(data))
            self.device.writeRegister(self.digitalModbusAddr, data)
        else:
            raise OSError(EACCES, 'Read only')

    def checkMode(self):
        if self.dirRef.state == 1:
            return 0664
        else:
            return 0444
    mode = property(checkMode)

class DeviceAttributePath(Path):
    def __init__(self, parent, myName, device, attr):
        super(DeviceAttributePath, self).__init__(parent, myName)
        self.fileType = "FILE"
        self.myAttrValue = str(getattr(device, attr)) + '\n'
        self.length = len(self.myAttrValue)

    def read(self):
        if DEBUG: print "DeviceAttributePath Reading attr " + self.myAttrValue
        return self.myAttrValue

class ReadmePath(Path):
    def __init__(self, parent, readmeStr, myName = "README.txt"):
        super(ReadmePath, self).__init__(parent, myName)
        self.fileType = "FILE"
        self.readmeStr = readmeStr
        self.length = len(readmeStr)
    
    def read(self):
        return self.readmeStr

class PathController(object):
    def __init__(self, dm):
        self.dm = dm
        self.buildPathDict()
    
    def buildPathDict(self):
        self.pathDict = dict()
        rootPath = RootPath()
        self.pathDict['/'] = rootPath
        topLevelReadme = ReadmePath(rootPath, TOP_LEVEL_README)
        self.pathDict['/README.txt'] = topLevelReadme
        howToUnmountDoc = ReadmePath(rootPath, howToUnmount(), myName="HOW_TO_UNMOUNT.txt")
        self.pathDict['/HOW_TO_UNMOUNT.txt'] = howToUnmountDoc

        names = self.dm.names()
        for name in names:
        
            # /device name/
            deviceNamePath = DeviceNamePath(rootPath, name)
            self.pathDict['/' + name] = deviceNamePath
            deviceLevelReadme = ReadmePath(deviceNamePath, DEVICE_LEVEL_README)
            self.pathDict['/' + name + "/README.txt"] = deviceLevelReadme
            thisDevice = self.dm.deviceByName[name]
            
            # /device name/serialNumber
            serialNumberPath = DeviceAttributePath(deviceNamePath, "serialNumber", thisDevice, "serialNumber")
            self.pathDict['/' + name + "/serialNumber"] = serialNumberPath
            
            # /device name/firmwareVersion
            firmwareVersionPath = DeviceAttributePath(deviceNamePath, "firmwareVersion", thisDevice, "firmwareVersion")
            self.pathDict['/' + name + "/firmwareVersion"] = firmwareVersionPath
            
            # /device name/modbus
            modbusOpPath = ModbusOpPath(deviceNamePath)
            self.pathDict['/' + name + "/modbus"] = modbusOpPath
            modbusLevelReadme = ReadmePath(modbusOpPath, MODBUS_LEVEL_README)
            self.pathDict['/' + name + "/modbus/README.txt"] = modbusLevelReadme
            for addr, mode in MODBUS_ADDRS.items():
                modbusAddrPath = ModbusAddrPath(modbusOpPath, addr, thisDevice, int(addr), mode)
                self.pathDict['/' + name + "/modbus/" + addr] = modbusAddrPath
                
            # /device name/connection
            connectionLabelOpPath = ConnectionLabelOpPath(deviceNamePath)
            self.pathDict['/' + name + "/connection"] = connectionLabelOpPath
            connectionLevelReadme = ReadmePath(connectionLabelOpPath, CONNECTION_LEVEL_README)
            self.pathDict['/' + name + "/connection/README.txt"] = connectionLevelReadme
            
            # /device name/connection/*
            if thisDevice.devType == 3:
                if thisDevice.deviceName == "U3-HV":
                    for label, t in U3_HV_CONNECTION_LABELS.items():
                        addr, mode = t
                        connectionLabelPath = ModbusAddrPath(connectionLabelOpPath, label, thisDevice, int(addr), mode)
                        self.pathDict['/' + name + "/connection/" + label] = connectionLabelPath
                    flexibleConnectionLabels = U3_HV_FLEXIBLE_CONNECTION_LABELS
                else:
                    for label, t in U3_LV_CONNECTION_LABELS.items():
                        addr, mode = t
                        connectionLabelPath = ModbusAddrPath(connectionLabelOpPath, label, thisDevice, int(addr), mode)
                        self.pathDict['/' + name + "/connection/" + label] = connectionLabelPath
                    flexibleConnectionLabels = U3_LV_FLEXIBLE_CONNECTION_LABELS
                for label, ioNumber in flexibleConnectionLabels.items():
                    dirLabel = label + "-dir"
                    flexibleIODirPath = FlexibleIODirPath(connectionLabelOpPath, dirLabel, thisDevice, ioNumber)
                    self.pathDict['/' + name + "/connection/" + dirLabel] = flexibleIODirPath
                    flexibleIOStatePath = FlexibleIOStatePath(connectionLabelOpPath, label, thisDevice, ioNumber, flexibleIODirPath)
                    self.pathDict['/' + name + "/connection/" + label] = flexibleIOStatePath
            else:
                for label, t in U6_UE9_CONNECTION_LABELS.items():
                    addr, mode = t
                    connectionLabelPath = ModbusAddrPath(connectionLabelOpPath, label, thisDevice, int(addr), mode)
                    self.pathDict['/' + name + "/connection/" + label] = connectionLabelPath


        if DEBUG: print "PathController buildPathDict self.pathDict =", self.pathDict

    def childrenNames(self, pathObj):
        return [c.myName for c in pathObj.children]

    def renameDevice(self, old, new):
        self.dm.renameDevice(old, new)
        self.buildPathDict()

def howToUnmount():
    unmountStr = """
LJFuse: How to unmount this filesystem
======================================

1. All programs (e.g., text-editors and terminal windows) must exit
directories in this filesysem. If you're reading this file from a 
terminal in this directory, run

    $ cd ..
"""
    if sys.platform == "darwin":
        unmountStr += """
Step 2. Eject it from the Finder or run

    $ umount %s

""" % "LJFuse"
    else:
        unmountStr += """Step 2. Unmount it with 
    $ fusermount -u %s

""" % mountPoint
    return unmountStr



TOP_LEVEL_README = """
LJFuse README.txt: Top level
============================
  At this level, there is one directory for every LabJack that LJFuse found
  when it started. Change to a directory to use that LabJack.
  
  Note: Restart LJFuse after disconnecting or reconnecting devices. LJFuse
  doesn't directly support hot-swapping. See the file HOW_TO_UNMOUNT.txt
  for instructions on how to restart LJFuse.

  LJFuse can connect to LabJack devices opened through LJSocket. See
  http://labjack.com/support/python/ljsocket

  Example:
    $ ls
    HOW_TO_UNMOUNT.txt  My U6/  README.txt
    $ cd "My U6/"

"""

DEVICE_LEVEL_README = """
LJFuse README.txt: Device level
===============================

  At this level, LJFuse lists the different ways of communicating with a
  LabJack device. The connection/ subdirectory contains files to access
  individual connections on a LabJack, and the modbus/ subdirectory
  contains files to access individual Modbus addresses. Files in this
  directory contains device-wide info, such as serial number and firmware
  version.
  
  Example:
    $ ls
    README.txt       firmwareVersion  serialNumber
    connection/      modbus/
    $ cat firmwareVersion
    1.15
    $ cd connection/

  Bonus: Renaming a device is supported. The syntax is
    mv <old name> <new name>
  Use it like this:
    $ cd ..
    $ ls
    HOW_TO_UNMOUNT.txt  My U6/  README.txt
    $ mv "My U6" "George"
    $ ls
    George/  HOW_TO_UNMOUNT.txt  README.txt
"""

MODBUS_LEVEL_README = """
LJFuse README.txt: Modbus level
===============================

  At this level, LJFuse presents files for Modbus addresses that
  applications can read and write to. The full LabJack Modbus map is
  available here: 
  
    http://labjack.com/support/modbus
  
  Briefly, here are Modbus addresses LJFuse uses:
  
    Modbus address        Action
    --------------        -------------------------
    0                     Read AIN0
    2                     Read AIN1
    4                     Read AIN2
    6                     Read AIN3
    5000                  Read/Write DAC0
    5002                  Read/Write DAC1
    6000                  Read/Write FIO0 state
    6001                  Read/Write FIO1 state
    ...                   ...
    6007                  Read/Write FIO7 state
    6100                  Read/Write FIO0 direction
    6101                  Read/Write FIO1 direction
    ...                   ...
    6107                  Read/Write FIO7 direction

  In the 6000 range, 0 means low and 1 means high. In the 6100 range,
  0 means input and 1 means output.
  
  The file permissions on each file denote which Modbus addresses are
  read-only and which ones allow writes.
  
  In the example below, wire a jumper from DAC0 to AIN0, and connect an
  LED on FIO2 and GND.
  
  Example:
    $ cat 5000 # Read DAC0
    3.00
    $ cat 0 # Read AIN0
    3.00
    $ echo 2.0 > 5000 # Set DAC0 to 2.0
    $ cat 5000
    2.00
    $ cat 0
    2.00
    $ cat 6102 # Read the direction of FIO2
    0
    $ echo 1 > 6102 # Set FIO2 to digital output
    $ cat 6002 # Read the state of FIO2
    0
    $ echo 1 > 6002 # Set FIO2 to high
    $ cat 6002
    1

"""

CONNECTION_LEVEL_README = """
LJFuse README.txt: Connection level
===================================

  At this level, LJFuse provides files to represent connections on a
  LabJack device. Check the file permissions to see which ones are 
  read-only (e.g., AIN0) and which ones are read-write (e.g., FIO4).

  In the example below, wire a jumper from DAC0 to AIN0, and connect an
  LED on FIO2 and GND.
  
  Example:
    $ cat DAC0 # Read DAC0
    2.000
    $ cat AIN0 # Read AIN0
    1.999
    $ echo 3.0 > DAC0 # Set DAC0 to 3.0 V
    $ cat DAC0 
    3.000
    $ cat AIN0
    3.000
    $ echo 1 > FIO2-dir # Set FIO2 to digital output
    $ cat FIO2 # Read the state of FIO2
    1    
    $ echo 0 > FIO2 # Set FIO2 to low
    $ cat FIO2
    0    
    # Which FIOs are set for digital input?
    $ grep 0 FIO?-dir
    FIO0-dir:0    
    FIO1-dir:0    
    # Which FIOs are set for digital output?
    $ grep 1 FIO?-dir
    FIO2-dir:1    
    FIO3-dir:1    
    FIO4-dir:1    
    FIO5-dir:1    
    FIO6-dir:1    
    FIO7-dir:1    

  U3 Note: The U3-LV has flexible inputs FIO0-FIO7, and the U3-HV has
  flexible inputs FIO4-FIO7. Here's how to set them to digital input (0),
  digital output (1), or analog input (2):
  U3 Example:
    $ ls
    DAC0        FIO1        FIO3        FIO5        FIO7
    DAC1        FIO1-dir    FIO3-dir    FIO5-dir    FIO7-dir
    FIO0        FIO2        FIO4        FIO6        README.txt
    FIO0-dir    FIO2-dir    FIO4-dir    FIO6-dir
    $ cat FIO0-dir # Check the direction of FIO0
    2
    $ cat FIO0 # Read analog input
    2.019
    $ echo 0 > FIO0-dir # Set the FIO0 direction to digital input
    $ cat FIO0 # Read digital input
    1    
    $ echo 1 > FIO0-dir # Set the FIO0 direction to digital output
    $ echo 0 > FIO0 # Set the FIO0 state to output low
    $ cat FIO0
    0    
    $ echo 1 > FIO0 # Set the FIO0 state to output high
    $ cat FIO0
    1    
    $ ls -l FIO0 # The permissions allow writing to a digital output
    -rw-rw-r--  0 mikec  staff  6 Jul 20 11:49 FIO0
    $ echo 0 > FIO0-dir
    $ ls -l FIO0  # The permissions don't allow writing to an input
    -r--r--r--  0 mikec  staff  6 Jul 20 11:50 FIO0
    $ echo 2 > FIO0-dir
    $ ls -l FIO0  # The permissions don't allow writing to an input
    -r--r--r--  0 mikec  staff  6 Jul 20 11:50 FIO0

"""



class DeviceManager(object):
    """
    The DeviceManager class will manage all the open connections to LJSocket

    Adapted from LabJack CloudDot Grounded
    """
    def __init__(self):
        self.address = LJSOCKET_ADDRESS
        self.port = LJSOCKET_PORT
        self.deviceBySerial = dict()
        self.deviceByName = dict()

        self.usbOverride = False

        try:
            self.updateDeviceDict()
        except Exception:
            self.usbOverride = True
            self.updateDeviceDict()
        
        if DEBUG: print "self.deviceByName =", self.deviceByName
        if DEBUG: print "self.deviceBySerial =", self.deviceBySerial

    def updateDeviceDict(self):
        if self.usbOverride:
            ljsocketAddress = None
            devs = list()

            devCount = LabJackPython.deviceCount(None)

            for serial, dev in self.deviceBySerial.items():
                dev.close()
                self.deviceBySerial.pop(serial)
            self.deviceByName = dict()

            devsObj = LabJackPython.listAll(3)
            for dev in devsObj.values():
                devs.append({"serial" : dev["serialNumber"], "prodId" : dev["devType"]})

            devsObj = LabJackPython.listAll(6)
            for dev in devsObj.values():
                devs.append({"serial" : dev["serialNumber"], "prodId" : dev["devType"]})

            devsObj = LabJackPython.listAll(9)
            for dev in devsObj.values():
                devs.append({"serial" : dev["serialNumber"], "prodId" : dev["devType"]})

            devsObj = LabJackPython.listAll(0x501)
            for dev in devsObj.values():
                devs.append({"serial" : dev["serialNumber"], "prodId" : dev["devType"]})

            if DEBUG: print "usbOverride:",devs

        else:
            ljsocketAddress = "%s:%s" % (self.address, self.port)
            devs = LabJackPython.listAll(ljsocketAddress, LabJackPython.LJ_ctLJSOCKET)

        serials = list()

        for dev in devs:
            serials.append(str(dev['serial']))

            if str(dev['serial']) in self.deviceBySerial:
                continue

            if dev['prodId'] == 3:
                if DEBUG: print "Adding new device with serial = %s" % (dev['serial'])
                try:
                    d = u3.U3(LJSocket = ljsocketAddress, serial = dev['serial'])
                except Exception, e:
                    raise Exception( "Error opening U3: %s" % e )

                try:
                    d.configU3()
                    d.getCalibrationData()
                except Exception, e:
                    raise Exception( "Error with configU3: %s" % e )

            elif dev['prodId'] == 6:
                try:
                    d = u6.U6(LJSocket = ljsocketAddress, serial = dev['serial'])
                    d.configU6()
                    d.getCalibrationData()
                except Exception, e:
                    if DEBUG: print "In opening a U6: %s" % e

            elif dev['prodId'] == 9:
                d = ue9.UE9(LJSocket = ljsocketAddress, serial = dev['serial'])
                d.commConfig()
                d.controlConfig()

            elif dev['prodId'] == 0x501:
                if DEBUG: print "Got a bridge... opening."
                d = bridge.Bridge(LJSocket = ljsocketAddress, serial = dev['serial'])
                d.ethernetFirmwareVersion()
                d.usbFirmwareVersion()
            else:
                raise Exception("Unknown device type")

            self.deviceBySerial["%s" % str(d.serialNumber)] = d
            self.deviceByName["%s" % str(d.name)] = d

        # Remove the disconnected devices
        for serial in self.deviceBySerial.keys():
            if serial not in serials:
                if DEBUG: print "Removing device with serial = %s" % serial
                dd = self.deviceBySerial[str(serial)]
                for name, nd in self.deviceByName.items():
                    if dd == nd:
                        self.deviceByName.pop(name)
                        break
                dd.close()
                self.deviceBySerial.pop(str(serial))
    
    def names(self):
        #return [str(d.name) for d in self.devices.values()]
        return self.deviceByName.keys()

    def renameDevice(self, old, new):
        dev = self.deviceByName[old]
        dev.name = new
        del(self.deviceByName[old])
        self.deviceByName[new] = dev

class LJFuse(LoggingMixIn, Operations):
    """Filesystem to access LabJack devices"""

    def __init__(self, pathController):
        self.pathController = pathController
        if DEBUG: print "LJFuse init"

    def getattr(self, path, fh=None):
        try:
            pathObj = self.pathController.pathDict[path]
        except KeyError:
            if DEBUG: print "LJFuse getattr no pathObj for path = ", path
            raise OSError(ENOENT, '')
        
        if DEBUG: print "LJFuse getattr pathObj = ", pathObj
        if pathObj.fileType == "DIR":
            st = dict(st_mode=(S_IFDIR | 0755), st_nlink=2)
        elif pathObj.fileType == "FILE":
            if hasattr(pathObj, "mode"):
                mode = pathObj.mode
            else:
                mode = 0444
            st = dict(st_mode=(S_IFREG | mode), st_size=pathObj.length)
        else:
            if DEBUG: print "LJFuse getattr unknown fileType", pathObj.fileType
        st['st_ctime'] = st['st_mtime'] = st['st_atime'] = time()
        st['st_uid'], st['st_gid'], pid = fuse_get_context()
        return st
        

    def readdir(self, path, fh):
        try:
            pathObj = self.pathController.pathDict[path]
        except KeyError:
            if DEBUG: print "LJFuse readdir no pathObj for path = ", path
            raise OSError(ENOENT, '')
        
        directoryContents = ['.', '..']
        
        directoryContents += self.pathController.childrenNames(pathObj)
        return directoryContents

    def rename(self, old, new):
        if DEBUG: print "LJFuse rename old = ", old
        if DEBUG: print "LJFuse rename new = ", new
        try:
            pathObj = self.pathController.pathDict[old]
        except KeyError:
            if DEBUG: print "LJFuse rename no pathObj for old path = ", old
            raise OSError(ENOENT, '')

        if DEBUG: print "LJFuse rename pathObj = ", pathObj

        if isinstance(pathObj, DeviceNamePath):
            oldName = pathObj.myName
            # The new name is everything after the last /
            # Can't have a name with slashes in it
            newName = new.split('/')[-1] 
            self.pathController.renameDevice(oldName, newName)
        else:
            raise OSError(EACCES, "Rename not allowed")

    def read(self, path, size, offset, fh):
        try:
            pathObj = self.pathController.pathDict[path]
        except KeyError:
            if DEBUG: print "LJFuse read no pathObj for path = ", path
            raise OSError(ENOENT, '')

        if DEBUG: print "LJFuse read pathObj = ", pathObj


        return pathObj.read()


    def truncate(self, path, length, fh=None):
        try:
            pathObj = self.pathController.pathDict[path]
        except KeyError:
            if DEBUG: print "LJFuse truncate no pathObj for path = ", path
            raise OSError(ENOENT, '')

        if DEBUG: print "LJFuse truncate pathObj = ", pathObj

    def write(self, path, data, offset, fh):
        try:
            pathObj = self.pathController.pathDict[path]
        except KeyError:
            if DEBUG: print "LJFuse write no pathObj for path = ", path
            raise OSError(ENOENT, '')

        if DEBUG: print "LJFuse write pathObj = ", pathObj

        if hasattr(pathObj, "write"):
            pathObj.write(data)
        else:
            raise OSError(EACCES, 'Read only')
        return len(data)

    # Disable unused operations:
    #access = None  # Need this one for rename
    flush = None
    getxattr = None
    listxattr = None
    open = None
    opendir = None
    release = None
    releasedir = None
    statfs = None

if __name__ == "__main__":
    if len(sys.argv) == 1:
        mountPoint = DEFAULT_MOUNT_POINT
        if not os.path.isdir(mountPoint):
            print "Making directory", mountPoint, "for LJFuse"
            os.mkdir(mountPoint)
    elif len(sys.argv) == 2:
        mountPoint = sys.argv[1]
        if not os.path.isdir(mountPoint):
            print "%s: No such directory. Create it first." % mountPoint
            sys.exit(1)
    else:
        print 'usage: %s [mountpoint]' % sys.argv[0]
        sys.exit(1)
    dm = DeviceManager()
    pathController = PathController(dm)
    kwargs = dict()
    if DEBUG: kwargs['foreground'] = True
    print "Mounting LJFuse at %s." % mountPoint
    if sys.platform == "darwin":
        unmountStr = "When done, eject it from the Finder or run `umount %s' (without quotes)." % "LJFuse"
        kwargs['volname'] = "LJFuse"
        kwargs['nolocalcaches'] = True
        if os.path.isfile("labjack-icon.icns"):
            kwargs['volicon'] = "labjack-icon.icns"
    else:
        unmountStr = "Unmount it with `fusermount -u %s' (without quotes)." % mountPoint
    print unmountStr
    fuse = FUSE(LJFuse(pathController), mountPoint, **kwargs)
