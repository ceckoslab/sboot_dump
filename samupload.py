#!/usr/bin/env python3
'''
Licensed under MIT License, (c) B. Kerler
'''
import os
import sys
import time
import argparse
import usb.core  # pyusb
import usb.util
from struct import unpack, pack, calcsize
from io import BytesIO
import logging
import usb.backend.libusb0
import usb.backend.libusb1
import datetime as dt
from ctypes import c_void_p, c_int
import shutil

USB_DIR_OUT = 0  # to device
USB_DIR_IN = 0x80  # to host

# USB types, the second of three bRequestType fields
USB_TYPE_MASK = (0x03 << 5)
USB_TYPE_STANDARD = (0x00 << 5)
USB_TYPE_CLASS = (0x01 << 5)
USB_TYPE_VENDOR = (0x02 << 5)
USB_TYPE_RESERVED = (0x03 << 5)

# USB recipients, the third of three bRequestType fields
USB_RECIP_MASK = 0x1f
USB_RECIP_DEVICE = 0x00
USB_RECIP_INTERFACE = 0x01
USB_RECIP_ENDPOINT = 0x02
USB_RECIP_OTHER = 0x03
# From Wireless USB 1.0
USB_RECIP_PORT = 0x04
USB_RECIP_RPIPE = 0x05

tag = 0

class usb_class():

    def load_windows_dll(self):
        if os.name == 'nt':
            windows_dir = None
            try:
                # add pygame folder to Windows DLL search paths
                windows_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)))
                try:
                    os.add_dll_directory(windows_dir)
                except Exception as err:
                    pass
                os.environ['PATH'] = windows_dir + ';' + os.environ['PATH']
            except Exception:
                pass
            del windows_dir

    def __init__(self, loglevel=logging.INFO, portconfig=None, devclass=-1):
        self.load_windows_dll()
        self.connected = False
        self.timeout = 1000
        self.vid = None
        self.pid = None
        self.stopbits = None
        self.databits = None
        self.interface = None
        self.parity = None
        self.baudrate = None
        self.EP_IN = None
        self.EP_OUT = None
        self.configuration = None
        self.device = None
        self.loglevel = loglevel
        self.portconfig = portconfig
        self.devclass = devclass
        self.info = print
        self.error = print
        self.warning = print
        self.debug = print

        if sys.platform.startswith('freebsd') or sys.platform.startswith('linux'):
            self.backend = usb.backend.libusb1.get_backend(find_library=lambda x: "libusb-1.0.so")
        elif sys.platform.startswith('win32'):
            if calcsize("P") * 8 == 64:
                self.backend = usb.backend.libusb1.get_backend(find_library=lambda x: "libusb-1.0.dll")
            else:
                self.backend = usb.backend.libusb1.get_backend(find_library=lambda x: "libusb32-1.0.dll")
        if self.backend is not None:
            try:
                self.backend.lib.libusb_set_option.argtypes = [c_void_p, c_int]
                self.backend.lib.libusb_set_option(self.backend.ctx, 1)
            except Exception as err:
                self.backend = None

    def getInterfaceCount(self):
        if self.vid is not None:
            self.device = usb.core.find(idVendor=self.vid, idProduct=self.pid, backend=self.backend)
            if self.device is None:
                self.debug("Couldn't detect the device. Is it connected ?")
                return False
            try:
                self.device.set_configuration()
            except Exception as err:
                self.debug(str(err))
                pass
            self.configuration = self.device.get_active_configuration()
            self.debug(2, self.configuration)
            return self.configuration.bNumInterfaces
        else:
            self.__logger.error("No device detected. Is it connected ?")
        return 0

    def connect(self, EP_IN=-1, EP_OUT=-1):
        if self.connected:
            self.close()
            self.connected = False
        for usbid in self.portconfig:
            vid = usbid[0]
            pid = usbid[1]
            interface = usbid[2]
            self.device = usb.core.find(idVendor=vid, idProduct=pid, backend=self.backend)
            if self.device is not None:
                self.vid = vid
                self.pid = pid
                self.interface = interface
                break

        if self.device is None:
            self.debug("Couldn't detect the device. Is it connected ?")
            return False

        try:
            self.configuration = self.device.get_active_configuration()
        except usb.core.USBError as e:
            if e.strerror == "Configuration not set":
                self.device.set_configuration()
                self.configuration = self.device.get_active_configuration()
            if e.errno == 13:
                self.backend = usb.backend.libusb0.get_backend()
                self.device = usb.core.find(idVendor=self.vid, idProduct=self.pid, backend=self.backend)
        if self.configuration is None:
            self.error("Couldn't get device configuration.")
            return False
        if self.interface == -1:
            for interfacenum in range(0, self.configuration.bNumInterfaces):
                itf = usb.util.find_descriptor(self.configuration, bInterfaceNumber=interfacenum)
                if self.devclass != -1:
                    if itf.bInterfaceClass == self.devclass:  # MassStorage
                        self.interface = interfacenum
                        break
                else:
                    self.interface = interfacenum
                    break

        self.debug(self.configuration)
        if self.interface > self.configuration.bNumInterfaces:
            print("Invalid interface, max number is %d" % self.configuration.bNumInterfaces)
            return False

        if self.interface != -1:
            itf = usb.util.find_descriptor(self.configuration, bInterfaceNumber=self.interface)
            try:
                if self.device.is_kernel_driver_active(0):
                    self.debug("Detaching kernel driver")
                    self.device.detach_kernel_driver(0)
            except Exception as err:
                self.debug("No kernel driver supported: " + str(err))
            try:
                usb.util.claim_interface(self.device, 0)
            except:
                pass

            try:
                if self.device.is_kernel_driver_active(self.interface):
                    self.debug("Detaching kernel driver")
                    self.device.detach_kernel_driver(self.interface)
            except Exception as err:
                self.debug("No kernel driver supported: " + str(err))
            try:
                if self.interface != 0:
                    usb.util.claim_interface(self.device, self.interface)
            except:
                pass

            if EP_OUT == -1:
                self.EP_OUT = usb.util.find_descriptor(itf,
                                                       # match the first OUT endpoint
                                                       custom_match=lambda e: \
                                                           usb.util.endpoint_direction(e.bEndpointAddress) ==
                                                           usb.util.ENDPOINT_OUT)
            else:
                self.EP_OUT = EP_OUT
            if EP_IN == -1:
                self.EP_IN = usb.util.find_descriptor(itf,
                                                      # match the first OUT endpoint
                                                      custom_match=lambda e: \
                                                          usb.util.endpoint_direction(e.bEndpointAddress) ==
                                                          usb.util.ENDPOINT_IN)
            else:
                self.EP_IN = EP_IN

            self.connected = True
            return True
        else:
            print("Couldn't find MassStorage interface. Aborting.")
            self.connected = False
            return False

    def close(self, reset=False):
        if self.connected:
            try:
                if reset:
                    self.device.reset()
                if not self.device.is_kernel_driver_active(self.interface):
                    # self.device.attach_kernel_driver(self.interface) #Do NOT uncomment
                    self.device.attach_kernel_driver(0)
            except Exception as err:
                self.debug(str(err))
                pass
            usb.util.dispose_resources(self.device)
            del self.device
            self.connected = False

    def write(self, command, pktsize=None):
        if pktsize is None:
            pktsize = self.EP_OUT.wMaxPacketSize
        if isinstance(command, str):
            command = bytes(command, 'utf-8')
        pos = 0
        if command == b'':
            try:
                self.EP_OUT.write(b'')
            except usb.core.USBError as err:
                error = str(err.strerror)
                if "timeout" in error:
                    # time.sleep(0.01)
                    try:
                        self.EP_OUT.write(b'')
                    except Exception as err:
                        self.debug(str(err))
                        return False
                return True
        else:
            i = 0
            while pos < len(command):
                try:
                    ctr = self.EP_OUT.write(command[pos:pos + pktsize])
                    if ctr <= 0:
                        self.info(ctr)
                    pos += pktsize
                except Exception as err:
                    self.debug(str(err))
                    # print("Error while writing")
                    # time.sleep(0.01)
                    i += 1
                    if i == 3:
                        return False
                    pass
        return True

    def usbread(self, resplen = -1):
        res = bytearray()
        timeout = 0
        loglevel = self.loglevel
        epr = self.EP_IN.read
        wMaxPacketSize = self.EP_IN.wMaxPacketSize
        extend = res.extend
        cond = True
        if resplen == -1:
            rlen = 0xFFFFFFFF
        else:
            rlen = resplen
        while len(res)<rlen:
            try:
                extend(epr(self.EP_IN.wMaxPacketSize))
                if len(res)<self.EP_IN.wMaxPacketSize:
                    break
            except usb.core.USBError as e:
                error = str(e.strerror)
                if "timed out" in error:
                    return res
                elif "Overflow" in error:
                    self.error("USB Overflow")
                    return b""
                else:
                    self.info(repr(e))
                    return b""
        return res

    def ctrl_transfer(self, bmRequestType, bRequest, wValue, wIndex, data_or_wLength):
        ret = self.device.ctrl_transfer(bmRequestType=bmRequestType, bRequest=bRequest, wValue=wValue, wIndex=wIndex,
                                        data_or_wLength=data_or_wLength)
        return ret[0] | (ret[1] << 8)

    class deviceclass:
        vid = 0
        pid = 0

        def __init__(self, vid, pid):
            self.vid = vid
            self.pid = pid

    def detectusbdevices(self):
        dev = usb.core.find(find_all=True, backend=self.backend)
        ids = [self.deviceclass(cfg.idVendor, cfg.idProduct) for cfg in dev]
        return ids

    def usbwrite(self, data, pktsize=None):
        if pktsize is None:
            pktsize = len(data)
        res = self.write(data, pktsize)
        # port->flush()
        return res

    def usbreadwrite(self, data, resplen):
        self.usbwrite(data)  # size
        # port->flush()
        res = self.usbread(resplen)
        return res

    def rdword(self, count=1, little=False):
        rev = "<" if little else ">"
        value = self.usbread(4 * count)
        data = unpack(rev + "I" * count, value)
        if count == 1:
            return data[0]
        return data

    def rword(self, count=1, little=False):
        rev = "<" if little else ">"
        data = []
        for _ in range(count):
            v = self.usbread(2)
            if len(v) == 0:
                return data
            data.append(unpack(rev + "H", v)[0])
        if count == 1:
            return data[0]
        return data

    def rbyte(self, count=1):
        return self.usbread(count)

class progress:
    def __init__(self, pagesize):
        self.progtime = 0
        self.prog = 0
        self.progpos = 0
        self.start = time.time()
        self.pagesize = pagesize

    def calcProcessTime(self, starttime, cur_iter, max_iter):
        telapsed = time.time() - starttime
        if telapsed > 0 and cur_iter > 0:
            testimated = (telapsed / cur_iter) * (max_iter)
            finishtime = starttime + testimated
            finishtime = dt.datetime.fromtimestamp(finishtime).strftime("%H:%M:%S")  # in time
            lefttime = testimated - telapsed  # in seconds
            return int(telapsed), int(lefttime), finishtime
        else:
            return 0, 0, ""

    def show_progress(self, prefix, pos, total, display=True):
        if total == 0:
            return
        prog = round(float(pos) / float(total) * float(100), 1)
        if prog == 0:
            self.prog = 0
            self.start = time.time()
            self.progtime = time.time()
            self.progpos = pos
            print_progress(prog, 100, prefix='Done',
                           suffix=prefix + ' (Sector 0x%X of 0x%X) %0.2f MB/s' %
                                  (pos // self.pagesize,
                                   total // self.pagesize,
                                   0), bar_length=50)

        if prog > self.prog:
            if display:
                t0 = time.time()
                tdiff = t0 - self.progtime
                datasize = (pos - self.progpos) / 1024 / 1024
                if datasize != 0 and tdiff != 0:
                    try:
                        throughput = datasize / tdiff
                    except:
                        throughput = 0
                else:
                    throughput = 0
                telapsed, lefttime, finishtime = self.calcProcessTime(self.start, prog, 100)
                hinfo = ""
                if lefttime > 0:
                    sec = lefttime
                    if sec > 60:
                        min = sec // 60
                        sec = sec % 60
                        if min > 60:
                            h = min // 24
                            min = min % 24
                            hinfo = "%02dh:%02dm:%02ds left" % (h, min, sec)
                        else:
                            hinfo = "%02dm:%02ds left" % (min, sec)
                    else:
                        hinfo = "%02ds left" % sec
                if hinfo != "":
                    print_progress(prog, 100, prefix='Progress:',
                                   suffix=prefix + f' (Sector 0x%X of 0x%X, {hinfo}) %0.2f MB/s' %
                                          (pos // self.pagesize,
                                           total // self.pagesize,
                                           throughput), bar_length=50)
                else:
                    print_progress(prog, 100, prefix='Progress:',
                                   suffix=prefix + f' (Sector 0x%X of 0x%X) %0.2f MB/s' %
                                          (pos // self.pagesize,
                                           total // self.pagesize,
                                           throughput), bar_length=50)
                self.prog = prog
                self.progpos = pos
                self.progtime = t0

def print_progress(iteration, total, prefix='', suffix='', decimals=1, bar_length=100):
    """
    Call in a loop to create terminal progress bar
    @params:
        iteration   - Required  : current iteration (Int)
        total       - Required  : total iterations (Int)
        prefix      - Optional  : prefix string (Str)
        suffix      - Optional  : suffix string (Str)
        decimals    - Optional  : positive number of decimals in percent complete (Int)
        bar_length  - Optional  : character length of bar (Int)
    """
    if total == 0:
        return
    str_format = "{0:." + str(decimals) + "f}"
    percents = str_format.format(100 * (iteration / float(total)))
    filled_length = int(round(bar_length * iteration / float(total)))
    bar = '█' * filled_length + '-' * (bar_length - filled_length)

    sys.stdout.write('\r%s |%s| %s%s %s' % (prefix, bar, percents, '%', suffix))

    if iteration == total:
        sys.stdout.write('\n')
    sys.stdout.flush()


class structhelper_io:
    pos = 0

    def __init__(self, data: BytesIO = None):
        self.data = data

    def setdata(self, data, offset=0):
        self.pos = offset
        self.data = data

    def qword(self):
        dat = int.from_bytes(self.data.read(8), 'little')
        return dat

    def dword(self):
        dat = int.from_bytes(self.data.read(4), 'little')
        return dat

    def dwords(self, dwords=1):
        dat = [int.from_bytes(self.data.read(4), 'little') for _ in range(dwords)]
        return dat

    def short(self):
        dat = int.from_bytes(self.data.read(2), 'little')
        return dat

    def shorts(self, shorts):
        dat = [int.from_bytes(self.data.read(2), 'little') for _ in range(shorts)]
        return dat

    def bytes(self, rlen=1):
        dat = self.data.read(rlen)
        if dat == b'':
            return dat
        if rlen == 1:
            return dat[0]
        return dat

    def string(self, rlen=1):
        dat = self.data.read(rlen)
        return dat

    def getpos(self):
        return self.data.tell()

    def seek(self, pos):
        self.data.seek(pos)


class partitiontable():
    ptype = None
    pname = None
    pstart = None
    pend = None

    def __init__(self, data, mode=64):
        sh = structhelper_io(BytesIO(data))
        if mode == 64:
            self.ptype = sh.dword()
            self.pname = sh.bytes(12).rstrip(b"\x00").decode('utf-8')
            self.info = sh.qword()
            self.pstart = sh.qword()
            self.pend = sh.qword()
        elif mode == 32:
            self.ptype = sh.dword()
            self.pname = sh.bytes(12).rstrip(b"\x00").decode('utf-8')
            self.info = sh.dword()
            self.pstart = sh.dword()
            self.pend = sh.dword()

    def __repr__(self):
        return f"\"{self.pname}\" ({hex(self.pstart)},{hex(self.pend)})"


class samsung_upload():

    def __init__(self):
        self.cdc = None
        self.progress = progress(512)

    def connect(self):
        VENDOR_SAMSUNG = 0x04e8
        PRODUCT_MODEM = 0x685d
        portconfig = [[VENDOR_SAMSUNG, PRODUCT_MODEM, 1]]
        self.cdc = usb_class(loglevel=logging.INFO,portconfig=portconfig,devclass=10)
        self.cdc.connected = self.cdc.connect()
        if self.cdc.connected:
            self.cdc.write(b"PrEaMbLe\0")
            data = self.cdc.usbread(self.cdc.EP_IN.wMaxPacketSize)
            ack = b"AcKnOwLeDgMeNt"
            if data[:len(ack)] != ack:
                print("Sorry, but device isn't in upload mode !")
                exit(0)
            return True
        return False

    def bytetostr(self, data):
        return data.rstrip(b"\x00").decode('utf-8')

    def probe(self):
        self.cdc.write(b"PrObE\0")
        if os.name == 'nt':
            data = self.cdc.usbread(6*self.cdc.EP_IN.wMaxPacketSize)
        else:
            data = self.cdc.usbread()
        return self.get_probe_table(data)

    def get_probe_table(self, data):
        count = 0
        devicename = self.bytetostr(data[0:16])
        data = BytesIO(data)
        if devicename[0] != "+":
            mode = 32
            size = 0x1C
        else:
            mode = 64
            size = 0x28
            devicename = devicename[1:]

        print(f"\nProbed device:\n" +
              f"---------------\n" +
              f"{mode}-Bit, " +
              f"Devicename: \"{devicename}\"\n")

        probetable = []
        print("Detected upload areas:\n---------------------")
        data.seek(0x10)
        while data.tell() != data.getbuffer().nbytes:
            pt = partitiontable(data.read(size), mode)
            if pt.pstart == 0 and pt.pend == 0:
                break
            probetable.append(pt)
            print(f"{count}: {pt}")
            count += 1
        return probetable

    def command(self, command, ack=True):
        command += b"\0"
        self.cdc.write(command)
        if ack:
            tmp = self.cdc.usbread(self.cdc.EP_IN.wMaxPacketSize)
            if tmp not in [b"AcKnOwLeDgMeNt\x00",b"PoStAmBlE\x00"]:
                return False
        return True

    def download(self, area):
        if not "." in area.pname:
            filename = "%s_%x_%x.lst" % (area.pname, area.pstart, area.pend)
        else:
            filename = area.pname
        wfilename = os.path.join("memory", filename)
        self.progress.show_progress('File: \"%s\"' % filename, 0, area.pend-area.pstart+1, True)
        with open(wfilename, "wb") as wf:
            start = bytes(hex(area.pstart)[2:], 'utf-8')
            end = bytes(hex(area.pend)[2:], 'utf-8')
            if not self.command(b"PrEaMbLe"):
                return False
            if not self.command(start):
                return False
            if not self.command(end):
                return False
            if not self.command(b"DaTaXfEr", False):
                return False
            total = area.pend + 1 - area.pstart
            pos = 0
            while pos < total:
                self.progress.show_progress('File: \"%s\"' % filename, pos, area.pend - area.pstart+1, True)
                size = total - pos
                if size > 0x80000:
                    size = 0x80000
                data = self.cdc.usbread(size)
                self.command(b"AcKnOwLeDgMeNt", False)
                wf.write(data)
                pos += len(data)
            self.progress.show_progress('File: \"%s\"' % filename, area.pend - area.pstart+1, area.pend - area.pstart+1, True)


def main():
    parser = argparse.ArgumentParser(description='SUC - Samsung Upload Client (c) B.Kerler 2018-2021.')

    print("\nSUC - Samsung Upload Client v1.1 (c) B. Kerler 2018-2021, Email: info @ revskills.de")
    parser.add_argument(
        '--area', '-a',
        help='Select area to dump',
        default='')
    parser.add_argument(
        '--all', '-all',
        help='Download all areas',
        action="store_true")
    args = parser.parse_args()

    suc = samsung_upload()
    if suc.connect():
        areas = suc.probe()
        if os.path.exists("memory"):
            shutil.rmtree("memory")
        os.mkdir("memory")

        if args.area != "":
            print("\nDownloading ....\n-----------------")
            area = int(args.area)
            if len(areas) <= area:
                print("Sorry, but area number is too high")
                exit(0)
            suc.download(areas[area])
            suc.command(b"PoStAmBlE")
        elif args.all:
            print("\nDownloading ....\n-----------------")
            for area in areas:
                suc.download(area)
            suc.command(b"PoStAmBlE")
        else:
            print("\nRun 'samupload.py -all' to dump all areas")
            print("Run 'samupload.py -a [number]' to dump specific area")

    print("Done. Dumped memory has been written to memory directory.")


def test():
    with open("samsung_dump.bin", "rb") as rf:
        data = rf.read()
        suc = samsung_upload()
        probetable = suc.get_probe_table(data)


if __name__ == '__main__':
    main()
    #test()
