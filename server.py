import uuid
import threading
import time
from sockets_classes import MultiCastSocket, UdpSocket
from queue import Queue


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
    "CLIENT_PORT": 16000,
}

class Server:
    def __init__(self):
        self.physical_time = time.time()
        self.ProcessUUID = uuid.uuid4()
        self.server_starttime = time.time()
        self.DynamicDiscovery_timestamp = time.time()
        self.localhost = socket.gethostname()
        self.localip = socket.gethostbyname(self.localhost)

        self.message_type = dict()

        self.OnlineServerDetails = {
            "PPIDs": [], # Process ID
            "ServerIPs": [],
            "AktivitaetZeitstempel": [],

            "BoolOfPPID": [], # Bool Value of Higher PPIDs (True = higher; False = lower)
        }

        self.primary = False

        self.incoming_msgs_thread = MultiCastSocket(process_id=str(self.ProcessUUID))
        self.incoming_mssgs_udp_socket_thread = UdpSocket()
        

        self.console = ServerDetails(self.OnlineServerDetails, self.primary)

        self._discovery_mssg_uuids_of_server = {}
        self.messenger = MessageTemplate(self.ProcessUUID)
        print("My PPID (uuid4): " + str(self.ProcessUUID))
        # --------------------------------------------------------------------------------

    def run_threads(self):
        self.incoming_msgs_thread.start()
        self.incoming_mssgs_udp_socket_thread.start()
        self.console.start()

        # initial discovery broadcast
        self._dynamic_discovery(server_start=True)
        try:

            while True:
                self._updateLead()

                # Dynamic Discovery alle 10 Sekunden Intervalle
                self._dynamic_discovery(server_start=False)

                # Nachrichten von  MultiCast Group Socket
                if not self.incoming_msgs_thread.queue.empty():
                    self.handle_incoming_multicasts()

                # Nachrichten von  UDP Socket
                if not self.incoming_mssgs_udp_socket_thread.queue.empty():
                    self.handle_incoming_udp_socket_channel_frames()

        except Exception as e:
            print(e)

        finally:
            self.incoming_msgs_thread.join()
            self.incoming_mssgs_udp_socket_thread.join()


    def _registerNewMessage(self, frame):
        self.message_type[frame[3]] = frame[0]

    def _messageAlreadyRegistred(self, mssg_uuid):
        if mssg_uuid in self.message_type:
            return True
        else:
            return False

    def handle_incoming_multicasts(self):
        data_list = self.incoming_msgs_thread.queue.get()
        # Sende Discovery Nachricht an Clienten

        # Füge den Server ein, wenn nicht ein Unbekannter Server und nicht eigene MultiCast Nachricht
        if data_list[0] == "DISCOVERY" and data_list[1] == "SERVER" and data_list[2] != str(self.ProcessUUID) and  data_list[7] != self.localip:
            # Wenn keine bekannte Server, dann füge den Server in neue ein.
            if data_list[7] not in self.OnlineServerDetails["ServerIPs"]:
                self._addNode(data_list)
            else:
                self._updateServerBoard(data_list)

        # Sende Discovery Nachricht an Server der anfordert
        if data_list[0] == "DISCOVERY" and data_list[1] == "SERVER" and data_list[2] != str(self.ProcessUUID):
            self._ackDiscovery(data_list[3], data_list[7])


        # Sieger nachricht bearbeiten
        if data_list[0] == "VICTORY" and data_list[2] != str(self.ProcessUUID) and data_list[1] == "SERVER":
            print(data_list[7] + " hat sich als neuer Leader Server angekündigt")


    def handle_incoming_udp_socket_channel_frames(self):
        data_list = self.incoming_mssgs_udp_socket_thread.queue.get()
       
        if data_list[0] == "CHATMESSAGE" and not self._messageAlreadyRegistred(data_list[3]):
            SenderAndMessage = data_list[6].split(":")
            print(SenderAndMessage[0]+ " sended to Group Chat: " + SenderAndMessage[1])

    def outgoing_frame_creater(self, frame_list):
        frame = ";".join(frame_list)
        return frame

    def _dynamic_discovery(self, server_start):
        if len(self.OnlineServerDetails["PPIDs"]) == 0 and server_start == True:
            message_uuid = self._create_DiscoveryUUID()
            self.DynamicDiscovery_timestamp = time.time()
            self.messenger.dynamic_discovery_message(message_uuid, str(0))
        self._discoveryIntervall()

    def _discoveryIntervall(self):
        # for @starttime
        if (float(time.time()) - float(self.DynamicDiscovery_timestamp)) > 5 and len(
                self.OnlineServerDetails["PPIDs"]) == 0:
            message_uuid = self._create_DiscoveryUUID()
            self.DynamicDiscovery_timestamp = time.time()
            self.messenger.dynamic_discovery_message(message_uuid, str(0))
        # for @futher discoveries
        if (float(time.time()) - float(self.DynamicDiscovery_timestamp)) > 10 and len(
                self.OnlineServerDetails["PPIDs"]) > 0:
            message_uuid = self._create_DiscoveryUUID()
            self.DynamicDiscovery_timestamp = time.time()
            self.messenger.dynamic_discovery_message(message_uuid, str(0))

    def _create_DiscoveryUUID(self):
        message_uuid = uuid.uuid4()
        self._discovery_mssg_uuids_of_server[str(message_uuid)] = False
        return message_uuid

    def _addNode(self, frame_list):
        self.OnlineServerDetails["PPIDs"].append(frame_list[2])
        self.OnlineServerDetails["ServerIPs"].append(frame_list[7])
        self.OnlineServerDetails["AktivitaetZeitstempel"].append(float(time.time()))

        if str(self.ProcessUUID) < str(frame_list[2]):
            self.OnlineServerDetails["BoolOfPPID"].append(True)
        else:
            self.OnlineServerDetails["BoolOfPPID"].append(False)

    def _updateServerBoard(self, frame_list):
        index = self.OnlineServerDetails["ServerIPs"].index(frame_list[7])
        self.OnlineServerDetails["PPIDs"][index] = frame_list[2]
        self.OnlineServerDetails["AktivitaetZeitstempel"][index] = float(time.time())
        if str(self.ProcessUUID) < str(frame_list[2]):
            self.OnlineServerDetails["BoolOfPPID"][index] = True
        else:
            self.OnlineServerDetails["BoolOfPPID"][index] = False

    def _ackDiscovery(self, discovery_mssg_uuid, receiver):
        self.messenger.ack_dynamic_discovery_message(discovery_mssg_uuid, receiver)

    def _ackClientDiscovery(self, discovery_mssg_uuid, receiver):
        self.messenger.ack_client_dynamic_discovery_message(discovery_mssg_uuid, receiver)


class ServerDetails(threading.Thread):

    def __init__(self, board, primary):
        super(ServerDetails, self).__init__()
        self.board = board
        self.primary = primary
        self.primaryppid = ""


    def run(self):
        while True:
            time.sleep(8.0)
            print("Primary: " + str(self.primary))
            print("The Primary PID is: " + self.primaryppid)
            print("Following Servers are online: " + str(self.board["ServerIPs"]))

import socket


group = ServerEinstellungen["MULTICAST_GROUP"]
port_mc = ServerEinstellungen["MCAST_PORT"]
port_udp_unicast = ServerEinstellungen["UDP_SOCKET_PORT"]
# 2-hop restriction in network
ttl = 2

def message_input(frame, sender_ip):
    framepositions = frame.split(";")
    framepositions.append(sender_ip)
    return framepositions

def outgoing_frame_creater(frame_list):
    frame = ";".join(frame_list)
    return frame

class MessageTemplate:

    def __init__(self, process_uuid4):
        self.UUID = process_uuid4
        # dynamic discovery message socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        # single shot udp
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # UDP

        self.dynamic_discovery_template = {
            "MESSAGE_TYPE": "DISCOVERY",
            "NODE_TYPE": "SERVER",
            "PROCESS_UUID64": str(self.UUID),
            "MSSG_UUID64": "",
            "LOGICAL_CLOCK": "",
            "PHYSICAL_CLOCK": "",
            "STATEMENT": "WHO IS THERE?",
        }


        self.dynamic_discovery_ack_template = {
            "MESSAGE_TYPE": "ACK",
            "NODE_TYPE": "SERVER",
            "PROCESS_UUID64": str(self.UUID),
            "MSSG_UUID64": "",
            "LOGICAL_CLOCK": "",
            "PHYSICAL_CLOCK": "",
            "STATEMENT": "I AM HERE",
        }

        self.dynamic_client_discovery_ack_template = {
            "MESSAGE_TYPE": "ACK",
            "NODE_TYPE": "SERVER",
            "PROCESS_UUID64": "",
            "MSSG_UUID64": "",
            "LOGICAL_CLOCK": "",
            "PHYSICAL_CLOCK": "",
            "STATEMENT": "I AM THE PRIMARY SERVER",
        }

        self.client_transmission_ack_template = {
            "MESSAGE_TYPE": "ACK",
            "NODE_TYPE": "SERVER",
            "PROCESS_UUID64": "",
            "MSSG_UUID64": "",
            "LOGICAL_CLOCK": "",
            "PHYSICAL_CLOCK": "",
            "STATEMENT": "MESSAGE CONFIRMATION",
        }

    def dynamic_discovery_message(self, message_uuid, current_clock):
        self.dynamic_discovery_template["MSSG_UUID64"] = str(message_uuid)
        self.dynamic_discovery_template["LOGICAL_CLOCK"] = current_clock
        self.sock.sendto(str.encode(
            outgoing_frame_creater(list(self.dynamic_discovery_template.values()))),
            (group, port_mc))

    def multicast_hearbeat(self, list_frame):
        self.sock.sendto(str.encode(
            outgoing_frame_creater(list_frame)), (group, port_mc))

    def ack_dynamic_discovery_message(self, ack_to_mssg, receiver):
        self.dynamic_discovery_ack_template["MSSG_UUID64"] = ack_to_mssg
        self.udp_sock.sendto(str.encode(
            outgoing_frame_creater(list(self.dynamic_discovery_ack_template.values()), )),
            (receiver, port_udp_unicast))

    def ack_client_dynamic_discovery_message(self, ack_to_mssg, receiver):
        self.dynamic_client_discovery_ack_template["MSSG_UUID64"] = ack_to_mssg
        self.udp_sock.sendto(str.encode(
            outgoing_frame_creater(list(self.dynamic_client_discovery_ack_template.values()), )),
            (receiver, 12222))

    def client_transmission_ack_message(self, ack_to_mssg, receiver):
        self.client_transmission_ack_template["MSSG_UUID64"] = ack_to_mssg
        self.udp_sock.sendto(str.encode(
            outgoing_frame_creater(list(self.client_transmission_ack_template.values()), )),
            (receiver, 12222))

    def coordinator_mssg(self, frame_list):  # multicast
        self.sock.sendto(str.encode(
            outgoing_frame_creater(frame_list)), (group, port_mc))


if __name__ == "__main__":
    server = Server()
    server.run_threads()