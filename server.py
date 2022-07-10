import uuid
import threading
import time
import socket

from sockets_classes import MultiCastSocket, UdpSocket
from queue import Queue

# Konfigurations parameter für jeden einzelnen Server:
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

group = ServerEinstellungen["MULTICAST_GROUP"]
port_mc = ServerEinstellungen["MCAST_PORT"]
port_udp_unicast = ServerEinstellungen["UDP_SOCKET_PORT"]
# 2-hop restriction in network
ttl = 2

# Server klasse
class Server:
    def __init__(self):
        self.ProcessUUID = uuid.uuid4() # dem Server wird ein Prozess ID bei Start zugewiesen (uuid)
        self.DynamicDiscovery_timestamp = time.time()# Server starttime für Dynamic Discovery(ist relevant für die Intervalle)
        self.localhost = socket.gethostname() # loopback adresse (127.0.0.1)
        self.localip = socket.gethostbyname(self.localhost) # eigene IP adresse

        # Liste aller messages inklv. mit Typ
        self.message_type = dict()

        # chronologisch geordnet
        self.OnlineServerDetails = {
            "PPIDs": [], # Process ID
            "ServerIPs": [], # IP der Server
            "AktivitaetZeitstempel": [], # Wichtig für Crash fault detection (Heartbeat intervall > 10?)
            "BoolOfPPID": [], # Bool Value of Higher PPIDs (True = higher; False = lower)
        }

        self.primary = False # (True= leader Server; False= backup server)
        self.election = False # (True = laufende Election -> manche Server abläufe sollen in der Zeit unterlassen werden)

        self.incoming_msgs_thread = MultiCastSocket(process_id=str(self.ProcessUUID)) # thread für multicastgroup nachrichten (226.226.226.226) : (Dynamic Discovery + Election + Victory + Heartbeat)
        self.incoming_mssgs_udp_socket_thread = UdpSocket() # Nachrichten für einzelne UDP Packete über eine IP+Portnummer (ACK von Discovery Nachrichten + Chatmessages => leader Server)
        self.election_thread = BullyAlgorithm(self.OnlineServerDetails, self.ProcessUUID, self.primary)

        self.console = ServerDetails(self.OnlineServerDetails, self.primary) #  für Ausgabe von einzelnen Variablen in der Console


        self._discovery_mssg_uuids_of_server = {} # Dynamic Discovery Message UUID werden hier registriert
        self.messenger = MessageTemplate(self.ProcessUUID) # Vorlage von Nachrichtenformat
        print("My PPID (uuid4): " + str(self.ProcessUUID))
        # --------------------------------------------------------------------------------

    def run_threads(self):
        self.incoming_msgs_thread.start()
        self.incoming_mssgs_udp_socket_thread.start()
        self.election_thread.start()
        self.console.start()

        # initial discovery broadcast
        self._dynamic_discovery(server_start=True)
        try:

            while True:
                self._updateLead()
                self.console.primaryppid = self.election_thread.primaryPID
                self.console.election_pending = self.election_thread.election_pending

                # Dynamic Discovery alle 10 Sekunden Intervalle (Falls 10 sekunden überschritten) dann nochmal versenden.
                self._dynamic_discovery(server_start=False)

                # Nachrichten von  MultiCast Group Socket
                if not self.incoming_msgs_thread.queue.empty():
                    self.handle_incoming_multicasts()

                # Nachrichten von  UDP Socket (chatmessages, ACK nachrichten)
                if not self.incoming_mssgs_udp_socket_thread.queue.empty():
                    self.handle_incoming_udp_socket_channel_frames()

                # Ausgehende Nachrichten von Election thread
                if not self.election_thread.outgoing_mssgs.empty():
                    self.create_outgoing_frame()
        except Exception as e:
            print(e)

        finally:
            self.incoming_msgs_thread.join()
            self.incoming_mssgs_udp_socket_thread.join()
            self.election_thread.join()

    # Jede Nachricht wird registriert:
    def _registerNewMessage(self, frame):
        self.message_type[frame[3]] = frame[0]

    # Funktion zur überprüfung ob die Nachricht bereits verarbeitet wurde (in list self.message_type)
    def _messageAlreadyRegistred(self, mssg_uuid):
        if mssg_uuid in self.message_type:
            return True
        else:
            return False

    # nehme aus der warteschleife (self.incoming_msgs_thread.queue.get()) eine datenliste und speichere in "data_list"
    def handle_incoming_multicasts(self):
        data_list = self.incoming_msgs_thread.queue.get()
        # Sende Discovery ACK Nachricht an Clienten als bestätigung
        # nur leader server antwortet auf discovery nachricht des clienten (da, self.election_thread.IchBinLeaderServer())
        if data_list[0] == "DISCOVERY" and data_list[1] == "CLIENT" and self.election_thread.IchBinLeaderServer():
            self._ackClientDiscovery(data_list[3], data_list[7])

        # Füge den Server ein, wenn nicht ein Unbekannter Server und nicht eigene MultiCast Nachricht
        if data_list[0] == "DISCOVERY" and data_list[1] == "SERVER" and data_list[2] != str(self.ProcessUUID) and data_list[7] != self.localip:
            # Wenn keine bekannte Server, dann füge den Server in neue ein.
            if data_list[7] not in self.OnlineServerDetails["ServerIPs"]:
                self._addNode(data_list)
            else:
                self._updateServerBoard(data_list)

        # Sende Discovery ACK Nachricht an Server der anfordert
        if data_list[0] == "DISCOVERY" and data_list[1] == "SERVER" and data_list[2] != str(self.ProcessUUID):
            self.election_thread.updateLastActivity(data_list)
            self._ackDiscovery(data_list[3], data_list[7])

        # Überwache Heartbeat Nachricht und verschiebe die Nachricht in Election Thread (falls HB ausfällt -> Election Thread startet Election,  da Leader Server tot)
        if data_list[0] == "HEARTBEAT" and data_list[2] != str(self.ProcessUUID) and  data_list[2] in self.OnlineServerDetails["PPIDs"] and data_list[1] == "SERVER" and  data_list[7] in self.OnlineServerDetails["ServerIPs"]:
            self.election_thread.incoming_mssgs.put(data_list)

        # Sieger nachricht bearbeiten
        if data_list[0] == "VICTORY" and data_list[2] != str(self.ProcessUUID) and data_list[1] == "SERVER":
            self.election_thread.incoming_mssgs.put(data_list)
            print(data_list[7] + " hat sich als neuer Leader Server angekündigt")

    # Erstelle ausgehende Nachrichten
    def create_outgoing_frame(self):
        data_list = self.election_thread.outgoing_mssgs.get()
        if data_list[0] == "HEARTBEAT":
            self.messenger.multicast_hearbeat(data_list[0:7])  # multicast
        if data_list[0] == "ELECTION":
            self.messenger.election_mssg(data_list[0:7], data_list[7])  # unicast
        if data_list[0] == "ACK":  # to do
            self.messenger.ack_election_mssg(data_list[0:7], data_list[7])  # unicast
        if data_list[0] == "VICTORY":
            self.messenger.coordinator_mssg(data_list[0:7])  # multicast


    def handle_incoming_udp_socket_channel_frames(self):
        data_list = self.incoming_mssgs_udp_socket_thread.queue.get()
        if data_list[0] == "ACK" and data_list[1] == "SERVER" and data_list[2] != str(self.ProcessUUID) and data_list[3] != self.election_thread.ELECTION_BOARD["electionID"]:
            if data_list[7] not in self.OnlineServerDetails["ServerIPs"]:
                self._addNode(data_list)
                self._discovery_mssg_uuids_of_server[data_list[7]] = True
            else:
                self._updateServerBoard(data_list)
        if data_list[0] == "ACK" and data_list[3] == self.election_thread.ELECTION_BOARD["electionID"]:
            self.election_thread.incoming_mssgs.put(data_list)

        if data_list[0] == "ELECTION":
            self.election_thread.incoming_mssgs.put(data_list)

        if data_list[0] == "CHATMESSAGE" and not self._messageAlreadyRegistred(data_list[3]):
            SenderAndMessage = data_list[6].split(":")
            print(SenderAndMessage[0]+ " sended to Group Chat: " + SenderAndMessage[1])

            if self.election_thread.IchBinLeaderServer():
                self._registerNewMessage(data_list)
                self.messenger.client_transmission_ack_message(data_list[3], data_list[7])
                # multi cast to group of partipicants
                chat_message_template = {"MESSAGE_TYPE": "CHATMESSAGE","NODE_TYPE": "SERVER","PROCESS_UUID64": "","MSSG_UUID64": data_list[3],"LOGICAL_CLOCK": "","PHYSICAL_CLOCK": "", "STATEMENT": data_list[6],}
                t_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                t_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
                chat_message_template["MSSG_UUID64"] = str(data_list[3])
                chat_message_template["STATEMENT"] = data_list[6]
                t_sock.sendto(str.encode(self.outgoing_frame_creater(list(chat_message_template.values()))), ("229.229.229.229", 15000))

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
        # for @server starttime (einmalige ausführung bei server startzeitpunkt)
        if (float(time.time()) - float(self.DynamicDiscovery_timestamp)) > 5 and len(
                self.OnlineServerDetails["PPIDs"]) == 0:
            message_uuid = self._create_DiscoveryUUID()
            self.DynamicDiscovery_timestamp = time.time()
            self.messenger.dynamic_discovery_message(message_uuid, str(0))

        # for @futher discoveries (intervall überprüfung)
        if (float(time.time()) - float(self.DynamicDiscovery_timestamp)) > 10 and len(
                self.OnlineServerDetails["PPIDs"]) > 0:
            message_uuid = self._create_DiscoveryUUID() # dynamic discovery uuid wird generiert
            self.DynamicDiscovery_timestamp = time.time() # aktualisieren des timestamps
            self.messenger.dynamic_discovery_message(message_uuid, str(0)) #  nachricht wird generiert

    def _create_DiscoveryUUID(self):
        message_uuid = uuid.uuid4()
        self._discovery_mssg_uuids_of_server[str(message_uuid)] = False
        return message_uuid

    # dynamic discovery von host = neuer host der hinzugefügt werden muss
    def _addNode(self, frame_list):
        self.OnlineServerDetails["PPIDs"].append(frame_list[2]) # canonical datamodel position 2
        self.OnlineServerDetails["ServerIPs"].append(frame_list[7]) # canoniccal position 7 (sender)
        self.OnlineServerDetails["AktivitaetZeitstempel"].append(float(time.time())) # letzte aktivität =  eingangszeitpunkt der nachricht

        if str(self.ProcessUUID) < str(frame_list[2]): # unser ppid ist kleiner als der des versenders, dann (siehe unten True)
            self.OnlineServerDetails["BoolOfPPID"].append(True) # (unten)
        else:
            self.OnlineServerDetails["BoolOfPPID"].append(False)  # false falls unser ppid höher ist

    # aktualisieren der server details (self.OnlineServerDetails)
    # Server die bereits in der liste sind
    # die details der Server werden aktualisiert (siehe unten)
    # PPID, falls bei runterfahren und neustarten neuer PPID zugewiesen wird.
    # aktivitaet zeitstempel aktualieren.
    def _updateServerBoard(self, frame_list):
        index = self.OnlineServerDetails["ServerIPs"].index(frame_list[7]) # index des server ip wird herausgefiltert.
        self.OnlineServerDetails["PPIDs"][index] = frame_list[2] # neuer process uuid (falls neu)
        self.OnlineServerDetails["AktivitaetZeitstempel"][index] = float(time.time()) # zeitstempel
        if str(self.ProcessUUID) < str(frame_list[2]):
            self.OnlineServerDetails["BoolOfPPID"][index] = True
        else:
            self.OnlineServerDetails["BoolOfPPID"][index] = False

    def _ackDiscovery(self, discovery_mssg_uuid, receiver):
        self.messenger.ack_dynamic_discovery_message(discovery_mssg_uuid, receiver)

    def _ackClientDiscovery(self, discovery_mssg_uuid, receiver):
        self.messenger.ack_client_dynamic_discovery_message(discovery_mssg_uuid, receiver)

    def _updateLead(self):
        if str(self.ProcessUUID) == str(self.election_thread.primaryPID):
            self.console.primary = True
            self.console.primaryppid = self.election_thread.primaryPID
        if str(self.ProcessUUID) != str(self.election_thread.primaryPID):
            self.console.primary = False
        self.OnlineServerDetails = self.election_thread.OnlineServerDetails


class BullyAlgorithm(threading.Thread):

    def __init__(self, board_of_nodes, process_uuid, primary):
        super(BullyAlgorithm, self).__init__()
        self.primaryPID = ""
        self.bool_primary = primary
        self.incoming_mssgs = Queue() # eingehende nachrichten (wird durch server (hauptprozess) übertragen/zugewiesen; Heartbeat, Election, Victory messages)
        self.outgoing_mssgs = Queue() # für ausgehende nachrichten (hearbeat (wenn man primary ist), election, victory (bei sieg).
        self.last_heartbeat_timestamp = time.time() # Heartbeat Timestamp (3 sec. Intervall)

        self.OnlineServerDetails = board_of_nodes
        self.PROCESS_UUID = str(process_uuid)
        self.temps = ElectionTemplate(self.PROCESS_UUID) # nachrichten vorlagen für ELECTION, ACK(auf ELECTION), HEARTBEAT, VICTORY (coordinator message)

        # wenn eine election läuft, dann election_pending = true, um dynamic discovery nachrichten nicht zu berücksichtigen
        # annahme für eine saubere funktion des algorithmuses.
        self.election_pending = False

        self.election_timeout_timestamp = time.time()
        # höchste PPID wird eingetragen.
        # time out läuft.
        # falls der server (höchste PPID) sich nicht als sieger bekannt gibt, dann wird noch eine election initiert.
        self.ELECTION_BOARD = {
            "electionHighestPID": str(self.PROCESS_UUID),
            "electionID": "",
        }



    def run(self):
        # Election nach 4 Sekunden starten, da neue Server erst endeckt werden müssen
        time.sleep(4)
        try:
            while True:
                self.initateElection() # initiate election
                self.heartbeat() # sende Heartbeats als leader server # UDP Heartbeat Multicast instead KEEPALIVE message mit TCP
                self.monitorTimeout() # falls election läuft, um eventuell noch eine election zu initieren.
                self.detectCrash() # leader server crash überwachen.
                self.refreshBoardOfServers() # bis wir alleine mit höchster ppid da stehen oder ein andere server sich als coodinator (victory) bekannt gibt

                if not self.incoming_mssgs.empty():
                    data_list = self.incoming_mssgs.get()
                    self.handleMessages(data_list)

        except Exception as e:
            print(e)

    def initateElection(self):
        if self.primaryPID == "" and self.election_pending != True:
            print("Election gestartet")
            self.election_pending = True
            election_uuid = str(uuid.uuid4())
            self.ELECTION_BOARD["electionID"] = election_uuid
            if True in self.OnlineServerDetails["BoolOfPPID"] and len(self.OnlineServerDetails["PPIDs"]) > 0:
                self.sendMessageToHigherPPIDs(election_uuid) # sende nacchrichten an die höchsten ppids
                self.setTimeout() # setze ein timeout bis sie antworten.
            if not True in self.OnlineServerDetails["BoolOfPPID"]:
                self.broadcastVictory()
                self.releaseElection()

    def sendMessageToHigherPPIDs(self, election_uuid):
        for idx, val in enumerate(self.OnlineServerDetails["ServerIPs"]):
            if self.OnlineServerDetails["BoolOfPPID"][idx]:
                self.outgoing_mssgs.put(self.temps.getElectionTemp(election_uuid, val))

    def detectCrash(self):
        if self.primaryPID != str(self.PROCESS_UUID) and self.election_pending != True:
            primaryboardindex = self.OnlineServerDetails["PPIDs"].index(self.primaryPID)
            if (float(time.time() - float(self.OnlineServerDetails["AktivitaetZeitstempel"][primaryboardindex]))> ServerEinstellungen["ELECTION_TIMEOUT"]):
                print("Verbindung zum Leader Server verloren...")
                print("Verbindung zum Leader Server verloren...")
                # Lösche Leader Server aus der Liste
                del self.OnlineServerDetails["PPIDs"][primaryboardindex]
                del self.OnlineServerDetails["ServerIPs"][primaryboardindex]
                del self.OnlineServerDetails["AktivitaetZeitstempel"][primaryboardindex]
                del self.OnlineServerDetails["BoolOfPPID"][primaryboardindex]
                self.primaryPID = "" # kein ppid ist leader daher ""
                self.initateElection()
        if len(self.OnlineServerDetails["PPIDs"]) > 0:
            for index in range(0, len(self.OnlineServerDetails["PPIDs"])):
                if (float(time.time() - float(self.OnlineServerDetails["AktivitaetZeitstempel"][index])) > 13):
                    # Lösche Replika Server aus der Liste
                    print("Verbindung zu einer Replica Server verloren!")
                    print("Verbindung zu einer Replica Server verloren!")
                    del self.OnlineServerDetails["PPIDs"][index]
                    del self.OnlineServerDetails["ServerIPs"][index]
                    del self.OnlineServerDetails["AktivitaetZeitstempel"][index]
                    del self.OnlineServerDetails["BoolOfPPID"][index]

    def handleMessages(self, data_frame):
        # überprüfe HB Nachricht und setze Heartbeat Intervall des Server zurück
        if data_frame[0] == "HEARTBEAT":
            self.updateLastActivity(data_frame)

        if data_frame[0] == "ACK": # ack wird entgegengenommen, letzte aktivität wird aktualisiert und der timeout bis zur VICTORY nachricht wird definiert
            if data_frame[2] > str(self.ELECTION_BOARD["electionHighestPID"]):
                self.ELECTION_BOARD["electionHighestPID"] = data_frame[2]
                self.updateLastActivity(data_frame)
                self.setTimeout()

        # überprüfe Election Nachricht
        if data_frame[0] == "ELECTION":
            self.outgoing_mssgs.put(self.temps.getAckToElectionTemp(data_frame[3], data_frame[7])) #  sende I am Alive nachricht raus
            if data_frame[2] > str(self.PROCESS_UUID):
                self.setTimeout() # setze timeout
            if data_frame[2] < str(self.PROCESS_UUID):
                # wenn wir einziger server sind mit höchster ppid in self.OnlineServerDetails["BoolOfPPID"]
                if not True in self.OnlineServerDetails["BoolOfPPID"]:
                    self.broadcastVictory() # sich als coordinator bekannt geben.

        # überprüfe Victory Nachricht (PPID > als meine?)
        if data_frame[0] == "VICTORY":

            # falls ppid > als meine, dann erkenne victory nachricht als gültig an
            if data_frame[2] > str(self.PROCESS_UUID):
                print(data_frame[2] + " PPID hat die Election gewonnen")
                self.primaryPID = data_frame[2]
                self.releaseElection() # normale funktion des servers wird durch aufheben des election status freigegeben
            else:
                #  falls nicht größer als meine, dann initiere eine NEUE Election Nachricht
                self.initateElection()

    # Sende eine Election Victory Nachricht aus
    def broadcastVictory(self):
        if not True in self.OnlineServerDetails["BoolOfPPID"]:
            self.outgoing_mssgs.put(self.temps.getCoordinatorTemp())
            self.primaryPID = self.PROCESS_UUID
            self.releaseElection()

    # Durch anstehende Election die Verhinderung des normalen Serverbetriebs freigeben
    def releaseElection(self):
        self.ELECTION_BOARD["electionHighestPID"] = str(self.PROCESS_UUID)
        self.ELECTION_BOARD["electionID"] = ""
        self.election_pending = False

    # election nachricht verschicken und timestamp setzen => empfänger muss sich als sicher innerhalb des zeitfensters als sieger bekannt geben.
    def setTimeout(self):
        self.election_timeout_timestamp = time.time()

    # timeout wird überwacht
    def monitorTimeout(self):
        if self.election_pending == True:
            if (float(time.time() - float(self.election_timeout_timestamp)) > ServerEinstellungen["ELECTION_TIMEOUT"]) and self.ELECTION_BOARD["electionHighestPID"] != "":
                # resend election messages again until we have a winner
                self.sendMessageToHigherPPIDs(self.ELECTION_BOARD["electionID"])
            else:
                self.primaryPID = self.ELECTION_BOARD["electionHighestPID"]

    # netzwerk hosts/server aktualisieren/löschen aus der liste
    def refreshBoardOfServers(self):
        # if higher PID Nodes not responding then delete!
        for idx, val in enumerate(self.OnlineServerDetails["ServerIPs"]):
            if self.OnlineServerDetails["BoolOfPPID"][idx] == True and \
                    self.primaryPID == "" and \
                    (float(time.time() - float(self.OnlineServerDetails["AktivitaetZeitstempel"][idx]))) > 10: # wenn keine aktivität die letzten 10 sekunden gegeben sind
                del self.OnlineServerDetails["PPIDs"][idx]
                del self.OnlineServerDetails["ServerIPs"][idx]
                del self.OnlineServerDetails["AktivitaetZeitstempel"][idx]
                del self.OnlineServerDetails["BoolOfPPID"][idx]

    # sende ein Heartbeat als Leader Server
    def heartbeat(self):
        if (float(time.time() - float(self.last_heartbeat_timestamp))) > float(ServerEinstellungen["HEARTBEAT_INTERVAL"]):
            if len(self.OnlineServerDetails["PPIDs"]) > 0 and self.IchBinLeaderServer():
                print("<--- --- Heartbeat --- --->")
                self.outgoing_mssgs.put(self.temps.getHeartbeatTemp())
                self.last_heartbeat_timestamp = time.time()

    def updateLastActivity(self, frame_list):
        if frame_list[2] in str(self.OnlineServerDetails["PPIDs"]):
            index = self.OnlineServerDetails["PPIDs"].index(frame_list[2])
            self.OnlineServerDetails["AktivitaetZeitstempel"][index] = float(time.time())

    # aktualisiere den server mit höchsten pid in der election tafel
    def updateElectionBoard(self, data_list):
        if data_list[2] > self.ELECTION_BOARD["electionHighestPID"]:
            self.ELECTION_BOARD["electionHighestPID"] = data_list[2]

    # bool wert für // True = ich bin leader server; False = ich bin NICHT der Leader
    def IchBinLeaderServer(self):
        if str(self.PROCESS_UUID) == self.primaryPID and self.election_pending != True:
            return True
        else:
            return False


class ElectionTemplate():

    def __init__(self, process_uuid):
        self.process_uuid = process_uuid

        self.election_template = [
            "ELECTION",
            "SERVER",
            str(self.process_uuid),
            "",
            "",
            "",
            "ARE YOU ALIVE?",
            ""
        ]

        self.heartbeat_template = [
            "HEARTBEAT",
            "SERVER",
            str(self.process_uuid),
            "",
            "",
            "",
            "I AM ALIVE",
        ]

        self.coordinator_template = [
            "VICTORY",
            "SERVER",
            str(self.process_uuid),
            "",
            "",
            "",
            "I AM LEAD",
        ]

        self.ackElection_template = [
            "ACK",
            "SERVER",
            str(self.process_uuid),
            "",
            "",
            "",
            "I AM ALIVE",
            ""
        ]

    def getElectionTemp(self, newElectionUUID, receiver):
        self.election_template[3] = newElectionUUID
        self.election_template[7] = receiver
        return self.election_template

    def getHeartbeatTemp(self):
        return self.heartbeat_template

    def getCoordinatorTemp(self):
        return self.coordinator_template

    def getAckToElectionTemp(self, election_uuid, receiver):
        self.ackElection_template[3] = election_uuid
        self.ackElection_template[7] = receiver
        return self.ackElection_template


# Anzeige von Server Parametern
class ServerDetails(threading.Thread):

    def __init__(self, board, primary):
        super(ServerDetails, self).__init__()
        self.board = board
        self.primary = primary
        self.primaryppid = ""
        self.election_pending = False

    def run(self):
        while True:
            time.sleep(8.0)
            print("Primary: " + str(self.primary))
            print("The Primary PID is: " + self.primaryppid)
            print("Following Servers are online: " + str(self.board["ServerIPs"]))

# Vorlage von einzelnen Nachrichtentypen
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

    def election_mssg(self, frame_list, receiver):  # unicast
        self.udp_sock.sendto(str.encode(
            outgoing_frame_creater(frame_list)), (receiver, port_udp_unicast))

    def ack_election_mssg(self, frame_list, receiver):  # unicast
        self.udp_sock.sendto(str.encode(
            outgoing_frame_creater(frame_list)), (receiver, port_udp_unicast))

    def coordinator_mssg(self, frame_list):  # multicast
        self.sock.sendto(str.encode(
            outgoing_frame_creater(frame_list)), (group, port_mc))

# Splitten der eingeheneden Nachrichten (positionen 0-6) + hinzufügen der Absender IP adresse
def message_input(frame, sender_ip):
    framepositions = frame.split(";")
    framepositions.append(sender_ip)
    return framepositions

# Zusammenfügen der datenliste in eine Zeichenkette
def outgoing_frame_creater(frame_list):
    frame = ";".join(frame_list)
    return frame

if __name__ == "__main__":
    server = Server()
    server.run_threads()