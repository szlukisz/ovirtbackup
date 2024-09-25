import argparse
from paramiko import SSHClient
from paramiko.ssh_exception import NoValidConnectionsError
import configparser
from backup_lib import OvirtHandler, copy_file, main_logger, VM_LOGGER_FILE
import sys
import os
from datetime import datetime
from mailer import send_mail

REQUIRED_SECTIONS = ["CONNECTION", "DIRECTORIES", "TRANSFER", "VM", "MAIL"]
BACKUP_SECTIONS = ["SNAPSHOT", "SSH"]
RESTORE_SECTIONS = ["RESTORATION"]

REQUIRED_PARAMS = [
    "ca_file",
    "username",
    "password",
    "ovirt_url",
    "working_directory",
    "chunk_size",
    "vm_name",
]
RESTORE_PARAMS = ["storage_domain", "cluster_name", "template", "new_vm_name"]
BACKUP_PARAMS = [
    "backup_snapshot_description",
    "ssh_ip",
    "ssh_username",
    "ssh_password",
    "ssh_commands",
]
COPY_TO_LOCAL_PARAMS = ["local_directory"]
GLOBAL_LOGGER_FILE = "global_savior.log"
MAIL_SUBJECT = "[OLVM_BACKUP_KSAT] {{mode}} of {{vm_name}} on {{date}}: {{status}}"
MAIL_TEMPLATE = "mailbody.txt"


def get_config(setup_file):
    config = configparser.ConfigParser(interpolation=None)
    main_logger.info("Reading configuration file %s..." % setup_file)
    config.read(setup_file)
    main_logger.info("Read configuration file %s." % setup_file)
    return config


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        metavar="mode",
        type=str,
        help="backup or restore: specifies backup or restore mode.",
    )
    parser.add_argument(
        "-s",
        "--setup_file",
        metavar="setupfile",
        help="setup file containing all the paremeters.",
        required=True,
    )

    args = parser.parse_args()
    var_args = vars(args)
    return var_args


def check_directory(directory, create=True):
    if not os.path.isdir(directory):
        if create:
            main_logger.warning("Directory %s can not be found. Attempting to create." % directory)
            os.makedirs(directory)
            main_logger.info("Created directory %s" % directory)
        else:
            msg = "Directory %s can not be found" % directory
            raise ValueError(msg)


class SaviorJob:
    def __init__(self, mode, setup_file):
        main_logger.info("...Savior job initializing...")
        self.config = get_config(setup_file)
        self.mode = mode
        self.status = "UNKNOWN"
        self.command_counter = 0

        self.check_sections()
        self.get_config_params()
        self.vm_name = self.params["vm_name"]
        self.working_directory = os.path.join(self.params["working_directory"], self.vm_name)
        if "local_directory" in self.params:
            self.local_directory = os.path.join(self.params["local_directory"], self.vm_name)
        self.check_params()
        self.check_directories()
        self.connect_to_api()

    def execute(self):
        if self.mode in ["backuptemp", "backup"]:
            # self.snapshot_name = self.params['backup_snapshot_description'] + datetime.now().strftime("%m-%d-%Y|%H:%M:%S")
            self.snapshot_name = self.params["backup_snapshot_description"]
            main_logger.info("Working on backup mode for VM %s", self.vm_name)

            # ssh connection
            self.establish_connection_ssh()

            # self.check_backup_directory()
            self.get_backup_vm()
            self.remove_backup_snapshot()

            # start backup mode on server
            self.execute_command_ssh()

            self.add_backup_snapshot()

            # stop backup mode
            self.execute_command_ssh()
            self.close_connection_ssh()

            if self.mode == "backup":
                self.check_backup_directory()
                self.download_disks()
                self.save_vm_info()
                ##self.remove_backup_snapshot()

        elif self.mode == "restore":
            main_logger.info("Working on restore mode for VM %s", self.vm_name)
            self.new_vm_name = self.params["new_vm_name"]
            main_logger.info("VM will be restored under the name %s", self.new_vm_name)
            self.get_vm_settings()
            self.check_for_restored_vm()
            self.copy_to_local()
            self.vm_settings["name"] = self.new_vm_name
            storage_domain = self.params["storage_domain"]
            template = self.params["template"]
            cluster_name = self.params["cluster_name"]

            self.oh.add_vm_from_settings(
                self.vm_settings,
                storage_domain=storage_domain,
                template=template,
                cluster_name=cluster_name,
                directory=self.local_directory,
                commit=True,
            )

    def check_missing(self, required):
        missing = [x for x in required if x not in self.params]
        if len(missing) != 0:
            msg = "Did not find any values for parameter(s) %s in the config file" % ", ".join(
                missing
            )
            raise ValueError(msg)

    def check_sections(self):
        if self.mode == "backup" or self.mode == "backuptemp":
            self.required_sections = REQUIRED_SECTIONS + BACKUP_SECTIONS
        else:
            self.required_sections = REQUIRED_SECTIONS + RESTORE_SECTIONS

        self.sections = self.config.sections()

        missing_sections = [x for x in self.required_sections if x not in self.sections]
        if len(missing_sections) != 0:
            msg = "Missing %s section(s) in the config file" % ", ".join(missing_sections)
            raise ValueError(msg)

    def get_config_params(self):
        self.params = {}
        for section in self.config.sections():
            for key, value in self.config[section].items():
                if key == "ssh_commands":
                    self.params[key] = value.split(";")
                else:
                    self.params[key] = value

    def check_params(self):
        self.check_missing(REQUIRED_PARAMS)
        if self.mode == "backup" or self.mode == "backuptemp":
            self.check_missing(BACKUP_PARAMS)
        elif self.mode == "restore":
            self.check_missing(RESTORE_PARAMS)

    def connect_to_api(self):
        main_logger.info("Connecting to Ovirt API...")
        try:
            self.oh = OvirtHandler(
                url=self.params["ovirt_url"],
                username=self.params["username"],
                password=self.params["password"],
                ca_file=self.params["ca_file"],
                download_dir=self.working_directory,
                chunk_size=self.params["chunk_size"],
            )
            self.oh.connection.authenticate()
            main_logger.info("Successfully opened a session with the Ovirt API.")

        except Exception as _:
            msg = "An error occured contacting the Ovirt API"
            raise ValueError(msg)

    def check_directories(self):
        if self.mode == "backup":
            check_directory(self.working_directory, create=True)
        else:
            check_directory(self.working_directory)
            check_directory(self.local_directory, create=True)

    def establish_connection_ssh(self):
        ip = self.params["ssh_ip"]
        username = self.params["ssh_username"]
        password = self.params["ssh_password"]
        main_logger.info(f"Establishing ssh connection to server with ip: {ip}")
        try:
            self.client = SSHClient()
            self.client.load_system_host_keys()
            self.client.connect(ip, username=username, password=password)
            main_logger.info("Connected successfully")
        except NoValidConnectionsError as err:
            main_logger.error(f"Failed to establish a connection: {err}")

    def execute_command_ssh(self):
        if self.command_counter >= len(self.params["ssh_commands"]):
            main_logger.warning("No more commands to execute, closing connection...")
            self.close_connection_ssh()
        command = self.params["ssh_commands"][self.command_counter]
        main_logger.info(f"Executing command: {command}")
        stdin, stdout, stderr = self.client.exec_command(command)
        main_logger.info(f'STDOUT: {stdout.read().decode("utf8")}')
        main_logger.info(f'STDERR: {stderr.read().decode("utf8")}')

        self.command_counter += 1
        stdin.close()
        stdout.close()
        stderr.close()

    def close_connection_ssh(self):
        if self.client:
            self.client.close()
            main_logger.info(f"Closed ssh connection, ssh client: {self.client}")
        else:
            main_logger.warning("No currently running ssh connection")

    def get_backup_vm(self):
        vm_name = self.params["vm_name"]
        main_logger.info("Seeking VM with name %s..." % vm_name)
        vm = self.oh.get_vm_by_name(vm_name)
        if not vm:
            msg = "VM with name %s could not be found through the ovirt API" % vm_name
            raise ValueError(msg)
        else:
            self.vm = vm
            main_logger.info("Found VM with name %s." % vm_name)

    def check_backup_directory(self):
        # vm_name = self.params["vm_name"]
        if os.path.isdir(self.working_directory):
            main_logger.warning(
                "Directory %s already exists. Contents may be overwritten."
                % self.working_directory
            )
        else:
            self.check_and_create_directory(self.working_directory)

    def check_and_create_directory(self, directory):
        if not os.path.exists(directory):
            main_logger.info(f"Creating directory with appriopriate name: {directory}")
            os.mkdir(directory)

    def add_backup_snapshot(self):
        sd = self.snapshot_name
        vm_name = self.params["vm_name"]
        main_logger.info("Creating snapshot %s on VM %s" % (sd, vm_name))
        self.vm.add_snapshot(sd)
        main_logger.info("Snapshot %s added on VM %s." % (sd, vm_name))

    def save_vm_info(self):
        vm_name = self.params["vm_name"]
        main_logger.info("Saving information for VM %s..." % vm_name)
        self.vm.save_settings(save_dir=self.working_directory)

        main_logger.info("Information saved for VM %s" % vm_name)

    def download_disks(self):
        vm_name = self.params["vm_name"]
        main_logger.info("Downloading disks of VM %s..." % vm_name)
        main_logger.info(f"Downloadind disk of VM for snapshot {self.snapshot_name}")
        self.vm.download_snapshot_disks(
            snapshot_name=self.snapshot_name, download_dir=self.working_directory
        )
        main_logger.info("Disks downloaded successfully.")

    def remove_backup_snapshot(self):
        sd = self.snapshot_name
        vm_name = self.params["vm_name"]
        main_logger.info("Removing snapshot %s on VM %s" % (sd, vm_name))
        self.vm.remove_snapshot(sd)
        main_logger.info("Snapshot removed.")

    def get_vm_settings(self):
        vm_name = self.params["vm_name"]
        self.working_directory = os.path.join(self.params["working_directory"], vm_name)

        if not os.path.isdir(self.working_directory):
            raise ValueError("VM directory %s not found" % self.working_directory)

        self.vm_settings = self.oh.vm_settings_from_file(vm_name, save_dir=self.working_directory)

    def copy_to_local(self):
        local_directory = self.local_directory
        working_directory = self.working_directory

        main_logger.info(
            "Copying discs from working directory %s to temp directory %s"
            % (working_directory, local_directory)
        )
        files = [
            f
            for f in os.listdir(working_directory)
            if os.path.isfile(os.path.join(working_directory, f))
        ]

        for file in files:
            source_file = os.path.join(working_directory, file)
            dest_file = os.path.join(local_directory, file)
            main_logger.info("Transfering %s to %s" % (source_file, dest_file))
            copy_file(source_file, dest_file)

        main_logger.info("Discs copied to local directory.")

    def check_for_restored_vm(self):
        if self.oh.get_vm_by_name(self.new_vm_name):
            raise ValueError(
                "A VM with name %s already exists in the cluster. Consider removing it."
                " Terminating."
                % self.new_vm_name
            )

    def send_mail(self):
        main_logger.info("Sending email notification")
        server = self.params["smtp_server"]
        port = self.params["smtp_port"]
        password = self.params["smtp_password"]
        to = self.params["smtp_recipient"]
        sender = self.params["smtp_sender"]
        replaceWith = [
            ["{{status}}", self.status],
            ["{{vm_name}}", self.vm_name],
            ["{{date}}", datetime.now().strftime("%m-%d-%Y|%H:%M:%S")],
            ["{{mode}}", self.mode.title()],
        ]

        send_mail(
            sender=sender,
            server=server,
            port=port,
            body=MAIL_TEMPLATE,
            attachmentFile=VM_LOGGER_FILE,
            replaceWith=replaceWith,
            to=to,
            subject=MAIL_SUBJECT,
            password=password,
        )


if __name__ == "__main__":
    try:
        v = parse_arguments()
        c = SaviorJob(v["mode"], v["setup_file"])
        c.execute()
        c.status = "SUCCESS!"
        c.send_mail()
    except Exception as exc:
        main_logger.error(exc, exc_info=exc)
        if c:
            c.status = "ERROR!"
            c.send_mail()
        sys.exit(1)
