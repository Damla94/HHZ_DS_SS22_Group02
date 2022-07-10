import threading
from queue import Queue
import socket
import struct

localhost = socket.gethostname()
localip = socket.gethostbyname(localhost)


ServerEinstellungen = {
    # Multicast Gruppe für Server
    "MULTICAST_GROUP": "226.226.226.226",
    "MCAST_PORT": 5900,

    "BROAD_CAST_ADRESS": 2222,
    "BROADCAST_PORT": 5975,


    # CLIENTS static Port
    "UDP_SOCKET_PORT": 12222,
    #interval for lead server detection
    "HEARTBEAT_INTERVAL": 3.0,
    "ELECTION_TIMEOUT": 10.0,

    "CLIENT_PORT": 16000,
}

class MultiCastSocket(threading.Thread):

    def __init__(self, process_id):
        super(MultiCastSocket, self).__init__()
        # class specific
        self.queue = Queue()
        self.process_id = process_id
        # multicast socket
        self.MCAST_GRP = ServerEinstellungen["MULTICAST_GROUP"]
        self.MCAST_PORT = ServerEinstellungen["MCAST_PORT"]
        self.multicast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.multicast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.multicast_socket.bind(('', self.MCAST_PORT))
        self.mreq = struct.pack("4sl", socket.inet_aton(self.MCAST_GRP), socket.INADDR_ANY)
        self.multicast_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, self.mreq)
        self.mcl = self.multicast_socket


    def run(self):
        print("Listening to network multicasts...")
        try:
            while True:
                data, addr = self.mcl.recvfrom(1024)
                if data:
                    data_list = message_input(data.decode("utf-8"), str(addr[0]))
                    if data_list[2] != self.process_id and data_list[7].split(".")[0] == "192":
                        self.queue.put(data_list, block=False)
        except Exception as e:
            print(e)


class UdpSocket(threading.Thread):

    def __init__(self):
        super(UdpSocket, self).__init__()
        self.MY_HOST = socket.gethostname()
        self.MY_IP = socket.gethostbyname(self.MY_HOST)
        self.queue = Queue()
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # UDP
        self.udp_sock.bind(("", ServerEinstellungen["UDP_SOCKET_PORT"]))
        print("my udp socket port is " + str(ServerEinstellungen["UDP_SOCKET_PORT"]))

    def run(self):
        print("Listening to network udp_unicasts...")
        try:
            while True:
                data, addr = self.udp_sock.recvfrom(1024)
                if data:
                    self.queue.put(message_input(data.decode("utf-8"), str(addr[0])), block=False)
        except Exception as e:
            print(e)


def message_input(string, sender_ip):
    data_list = string.split(";")
    data_list.append(sender_ip)
    return data_list
