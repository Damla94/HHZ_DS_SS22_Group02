import datetime
import threading
import time
import uuid
from queue import Queue
import sys
import select
from client_udp_socket import ClientUDPSocket, ClientMultiCast
import socket

localhost = socket.gethostname()
localip = socket.gethostbyname(localhost)

def outgoing_frame_creater(frame_list):
    frame = ";".join(frame_list)
    return frame

# https://repolinux.wordpress.com/2012/10/09/non-blocking-read-from-stdin-in-python/

Client_Einstellungen = {
    "CHAT_MCGROUP": "229.229.229.229",
    "CHAT_MCGROUPPORT": 15000,
    "UDP_SOCKET_PORT": 12222,
}

class ChatPartpicant(threading.Thread):

    def __init__(self, UserName):
        super(ChatPartpicant, self).__init__()
        self.username = UserName
        self.outgoings_pipe = Queue()
        self.incomings_pipe = Queue()
        self.primaryIP = ""
        self.DynamicDiscoveryTimestamp = time.time()
        self.resendTimer = time.time()

        self.client_udp_socket = ClientUDPSocket(Client_Einstellungen["UDP_SOCKET_PORT"])
        self.client_multicast_socket = ClientMultiCast(self.username)
        #self.worker_thread = WorkerClass(self.username, self.incomings_pipe, self.outgoings_pipe, self.primaryIP)

        self.ackedChatMessage = [] # liste der bestätigten nachrichten
        self.unackedChatMessage = [] # liste der NICHT bestätigten nachrichten


        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

        # single udp socket
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def run(self):
        self.client_udp_socket.start()
        self.client_multicast_socket.start()

        try:
            while True:
                # wenn input bereit, dann mache etwas, sonst mache was anderes (else)
                while sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                    line = sys.stdin.readline()
                    if line:
                        self.sendToChat(line, self.username, "")
                    else:  # an empty line means stdin has been closed
                        exit(0)
                else:
                    # read mc from server
                    if not self.client_udp_socket.queue.empty():
                        data_list = self.client_udp_socket.queue.get()
                        if data_list[0]  == "ACK" and data_list[1] == "SERVER" and data_list[6] == "I AM THE PRIMARY SERVER":
                            self.primaryIP = data_list[7]
                    if not self.client_multicast_socket.queue.empty():
                        data_list = self.client_multicast_socket.queue.get() # nehme eine datenliste aus der warteschlange (queue)
                        if data_list[0] == "CHATMESSAGE" and data_list[1] == "SERVER": # wenn chatmessage und von server
                            statement = data_list[6].split(":") #username position 0 und nachricht position 1
                            if data_list[3] not in self.ackedChatMessage: # wenn nachricht nicht in der bestätigten ist
                                self.printMessage(statement[1], statement[0])
                            self.ackedChatMessage.append(data_list[3]) # aufnahme der nachricht in die liste der bestätigten
                            if len(self.unackedChatMessage) > 0: #
                                for item in range(0, len(self.unackedChatMessage)):
                                    if self.unackedChatMessage[item][2] == data_list[3]:
                                        del self.unackedChatMessage[item]
                                        break # beenden der schleife da nachricht nur einmal vorhanden ist
                    self.DynamicDiscoveryMessageGenerieren()
                    self.KontrolleUnbestaetigteUndSendeNeu()
        except Exception as e:
            print(e)

    # unacked nachrichten (nicht erfolgreich zugestellte nachrichten) werden in 10 sekunden intervallen wieder versendet
    def KontrolleUnbestaetigteUndSendeNeu(self):
        if (float(time.time() - float(self.resendTimer))) > 10:
            if len(self.unackedChatMessage) > 0:
                for message in range(0, len(self.unackedChatMessage)):
                    self.sendToChat(self.unackedChatMessage[message][0], self.unackedChatMessage[message][1], self.unackedChatMessage[message][2])
            self.resendTimer = time.time()

    def sendToChat(self, line, username, message_uuid):
        # add to unacked
        transmission_template = {
            "MESSAGE_TYPE": "CHATMESSAGE",
            "NODE_TYPE": "CLIENT",
            "PROCESS_UUID64": "",
            "MSSG_UUID64": "",
            "LOGICAL_CLOCK": "",
            "PHYSICAL_CLOCK": "",
            "STATEMENT": "",
        }

        if len(message_uuid) > 0:
            # resending message parameters
            transmission_template["MSSG_UUID64"] = message_uuid
            transmission_template["STATEMENT"] = username + ":" + line
        else:
            # new message
            new_uuid = str(uuid.uuid4())
            self.unackedChatMessage.append((line, username, new_uuid))
            transmission_template["MSSG_UUID64"] = new_uuid
            transmission_template["STATEMENT"] = username + ":" + line

        self.udp_sock.sendto(str.encode(outgoing_frame_creater(list(transmission_template.values()), )), (self.primaryIP, 12222))

    def printMessage(self, line, username):
        print("[" + str(datetime.datetime.now()) + "] - " + username + ": " + line, end="")

    def DynamicDiscoveryMessageGenerieren(self):
        if (float(time.time()) - float(self.DynamicDiscoveryTimestamp)) > 10:
            DynamicDiscoveryTemplate = {
                "MESSAGE_TYPE": "DISCOVERY",
                "NODE_TYPE": "CLIENT",
                "PROCESS_UUID64": "",
                "MSSG_UUID64": "",
                "LOGICAL_CLOCK": "",
                "PHYSICAL_CLOCK": "",
                "STATEMENT": "I NEED A LEAD SERVER TO TALK TO",
            }
            DynamicDiscoveryTemplate["MSSG_UUID64"] = str(uuid.uuid4())
            self.sock.sendto(str.encode(outgoing_frame_creater(list(DynamicDiscoveryTemplate.values()))), ("226.226.226.226", 5900))
            self.DynamicDiscoveryTimestamp = time.time()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        client = ChatPartpicant(sys.argv[1])
        client.run()
    else:
        print("User name is needed!")
        pass
