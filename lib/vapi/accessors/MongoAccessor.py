# IBM_PROLOG_BEGIN_TAG
#
# Copyright 2021 IBM International Business Machines Corp.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#           http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
#  implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
#  IBM_PROLOG_END_TAG
import logging
import subprocess
import signal
import json
import base64
import pymongo

collections = ["DataSets", "DLTasks", "TrainedModels", "ProjectGroups", "BGTasks", "DataSetCategories",
               "DataSetFiles", "DataSetFileLabels", "DataSetFileUserKeys", "DataSetTags", "DataSetActiontags",
               "DataSetFileActionLabels", "DataSetFileObjectLabels", "InferenceOps", "InferenceDetails",
               "WebAPIs", "UserDNNs", "DnnScripts", "DeployableBinaries", "DockerHostPorts", "SysUsers",
               "UploadOperations", "Tokens", "RegisteredApps"]


class MviMongoException(Exception):
    pass


class MongoAccessor:
    """ Class to provide a "direct" connection to an MVI Mongo DB instance.
    Supports connections in standalone and OCP environments depending upon inputs.
    """

    def __init__(self, creds=None, mongoService=None, cluster=None):
        self.mongoDbCreds = creds
        self.mongoService = mongoService
        self.cluster = cluster
        self.tunnelProcess = None
        self.mongoClient = None
        self.mviDatabase = None
        self.mviVersion = 1 if creds else 2

    def __enter__(self):
        """ Context Manager required method to work with `with` statement."""
        self.connectToMongo()
        return self

    def __exit__(self, exception_type, exception_value, exception_traceback):
        """ Context Manager required method to work with `with` statement."""
        logging.debug(f"MongoAccessor.__exit__; etype={exception_type}; evalue={exception_value}")
        self.close()

    def connectToMongo(self):
        logging.info("Connecting to mongo")

        user, passwd = self.getMongoDbCredentials()
        self.loginToDb(user, passwd, hostname=self.getMongoHostName())

    def getMongoDbCredentials(self):
        """ Returns mongoDB username and password based upon provided access info."""

        # If mongoDb credentials provided to the script, use those
        if self.mongoDbCreds is not None:
            return self.mongoDbCreds["userName"], self.mongoDbCreds["password"]

        # otherwise get credentials from the cluster
        cmdArgs = ["oc", "get", "secret", "vision-secrets", "-o", "json"]
        process = subprocess.Popen(cmdArgs, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        if process.returncode == 0:
            jsonData = json.loads(stdout.decode('utf-8'))
            userName = base64.b64decode(jsonData["data"]["mongodb-admin-username"]).decode("utf-8")
            password = base64.b64decode(jsonData["data"]["mongodb-admin-password"]).decode("utf-8")
            logging.debug(f"user={userName}, pw={password}")
        else:
            logging.error(f"Failed to get Mongo info -- {cmdArgs}")
            logging.error(f"output = {process.stderr}")
            raise MviMongoException(f"Could not get Mongo connection info.")

        return userName, password

    def getMongoHostName(self):
        """ getMongoHostName returns the host name to use for the mongo connection.
            The host name depends upon the type of execution environment and the target mongo.
            If the name of a mongodb service was provided, the host name is the mongoDb service name.
            if we are running in a "regular" shell, the host name will be "localhost", but we may need to
            establish the appropriate "tunnel" to the target mongo pod. For a standalone environment,
            the tunnel must already be established.
        """
        if self.mongoService:
            hostname = self.mongoService
        #elif self.cluster.isStandalone:
        #    hostname = "localhost"
        else:
            # Assume OCP cluster for now. May need to expand in the future to handle IBM Cloud Private clusters.
            hostname = "localhost"
            self.tunnelToOcpMongo()
        logging.debug(f"returning hostname '{hostname}'.")
        return hostname

    def tunnelToOcpMongo(self):
        """ Sets up a tunnel the mongoDB if backing up a post 8.0.0 database."""
        pod = self.getOcpMongoPod()
        if pod is None:
            raise MviMongoException("Could not find MongoDB Pod.")
        self.establishOcpTunnel(pod, 27017, 27017)

    def getOcpMongoPod(self):
        """ Returns the pod name of the running mongoDB pod in an OCP cluster."""
        pods = self.cluster.getPods("-mongodb-")
        return pods[0] if len(pods) > 0 else ""

    def establishOcpTunnel(self, mongoPod, remotePort, localPort):
        cmdArgs = ["oc", "port-forward", "--address",  "0.0.0.0", mongoPod, f"{localPort}", f"{remotePort}"]
        logging.debug(f"Setting tunnel to '{mongoPod}'")

        self.tunnelProcess = subprocess.Popen(cmdArgs, stdout=subprocess.PIPE)

    def loginToDb(self, user, passwd, hostname="localhost", database="DLAAS"):
        """ Logins into MongoDB and setups access to the DLAAS database."""
        logging.debug(f"logging into mongo db '{database}', at '{hostname}' as '{user}'")

        # The auth mechanism changed when mondogDB was upgraded during the move to MAS
        if self.mviVersion == 1:
            authProtocol = "MONGODB-CR"
        else:
            authProtocol = "SCRAM-SHA-1"

        uri = f"mongodb://{user}:{passwd}@{hostname}:27017/?authSource={database}&authMechanism={authProtocol}"
        self.mongoClient = pymongo.MongoClient(uri)
        logging.debug(f"db conn={self.mongoClient}")
        self.mviDatabase = self.mongoClient[database]

    def getMongoClient(self):
        return self.mongoClient

    def getMviDatabase(self):
        return self.mviDatabase

    def disconnectFromMongo(self):
        self.mongoClient.close()

    def close(self):
        if self.tunnelProcess:
            self.disconnectFromMongo()
            self.tunnelProcess.send_signal(signal.SIGTERM)
        self.tunnelProcess = None

