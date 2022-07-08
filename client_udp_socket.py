import threading
import socket
from queue import Queue
import struct

def message_input(string, sender_ip):
    data_list = string.split(";")
    data_list.append(sender_ip)
    return data_list


def outgoing_frame_creater(frame_list):
    frame = ";".join(frame_list)
    return frame


Client_Einstellungen = {
    "ChatMultiCastGroup": "229.229.229.229",
    "ChatUDPSocketPort": 15000
}


class ClientUDPSocket(threading.Thread):

    def __init__(self, port):
        super(ClientUDPSocket, self).__init__()
        # Keine Primary Server am Anfang vorhanden
        self.primary_IP = ""

        self.MY_HOST = socket.gethostname()
        self.MY_IP = socket.gethostbyname(self.MY_HOST)
        self.queue = Queue()
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # UDP
        self.udp_sock.bind(("", port))



    def run(self):
        print("Client UDP Socket started...")
        try:
            while True:
                data, addr = self.udp_sock.recvfrom(1024)
                if data:
                    self.queue.put(message_input(data.decode("utf-8"), str(addr[0])), block=False)
        except Exception as e:
            print(e)

class ClientMultiCast(threading.Thread):

    def __init__(self, process_id):
        super(ClientMultiCast, self).__init__()
        # class specific
        self.queue = Queue()
        self.process_id = process_id
        # multicast socket


        self.multicast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.multicast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.multicast_socket.bind(('', Client_Einstellungen["ChatUDPSocketPort"]))
        self.mreq = struct.pack("4sl", socket.inet_aton(Client_Einstellungen["ChatMultiCastGroup"]), socket.INADDR_ANY)
        self.multicast_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, self.mreq)
        #self.mcl = self.multicast_socket

    def run(self):
        print("Listening to network multicasts...")
        try:
            while True:
                data, addr = self.multicast_socket.recvfrom(1024)
                if data:
                    data_list = message_input(data.decode("utf-8"), str(addr[0]))
                    if data_list[2] != self.process_id and data_list[7].split(".")[0] == "192":
                        self.queue.put(data_list, block=False)
        except Exception as e:
            print(e)






