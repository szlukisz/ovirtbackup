import ovirtsdk4 as sdk
from ovirtsdk4 import types
import time
import requests
import os
from math import floor, log10
from datetime import datetime
import sys
import pickle
import subprocess
import json
import logging
import glob

URL = "https://ovirtengine.example.com/ovirt-engine/api"
USERNAME = "admin@internal"
PASSWORD = "mypassword"
CA_FILE = "ovirt-pki-resource.cer"
VM_NAME = "test_for_backup"
DOWNLOAD_DIRECTORY = "/backup"
SAVE_DIRECTORY = DOWNLOAD_DIRECTORY
CHUNK_SIZE = 1024 * 1024 * 10
REPORT_EVERY = 1e9
STORAGE_DOMAIN = "mystorage"

NEW_DISK_NAME = "vm_disk"
NEW_DESCRIPTION = "A new VM added by the backup script"
NEW_FORMAT = types.DiskFormat.RAW
NEW_SPARSE = False
NEW_BOOTABLE = True
NEW_STORAGE_DOMAIN_NAME = "mystorage"
RECOVERY_CLUSTER = "mycluser"
RECOVERY_TEMPLATE = "Blank"
VM_LOGGER_FILE = "savior.log"
GLOBAL_LOGGER_FILE = "global_savior.log"


def logger():
    logger = logging.getLogger("savior")

    output_file_handler = logging.FileHandler(VM_LOGGER_FILE, mode="w")
    global_file_handler = logging.FileHandler(GLOBAL_LOGGER_FILE)
    stdout_handler = logging.StreamHandler(sys.stdout)
    logger.addHandler(output_file_handler)
    logger.addHandler(global_file_handler)
    logger.addHandler(stdout_handler)
    logger.setLevel(logging.DEBUG)

    formatter_file = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    formatter_stdout = logging.Formatter("%(message)s")

    stdout_handler.setFormatter(formatter_stdout)
    output_file_handler.setFormatter(formatter_file)
    global_file_handler.setFormatter(formatter_file)
    stdout_handler.setLevel(logging.DEBUG)
    output_file_handler.setLevel(logging.DEBUG)
    global_file_handler.setLevel(logging.DEBUG)
    return logger


main_logger = logger()


def rate_str(rate):
    if log10(rate) < 3:
        return "%3.1f b/s" % rate
    elif log10(rate) < 6:
        return "%3.1f Kb/s" % (rate / 1e3)
    elif log10(rate) < 9:
        return "%3.1f Mb/s" % (rate / 1e6)
    else:
        return "%3.2f Gb/s" % (rate / 1e9)


def size_str(s):
    if log10(s) < 3:
        return "%3.0fB" % s
    elif log10(s) < 6:
        return "%3.1fKB" % (s / 1e3)
    elif log10(s) < 9:
        return "%3.1fMB" % (s / 1e6)
    else:
        return "%3.1fGB" % (s / 1e9)


class transfer_bar:
    def __init__(self, expected_size, report_every=REPORT_EVERY, size_of_bar=20):
        self.t0 = datetime.now()
        self.expected_size = expected_size
        self.previous = 0
        self.size_of_bar = size_of_bar
        self.report_every = report_every
        self.last_report = 0

    def bar(self, counter):
        percentage = counter / self.expected_size
        bars_completed = int(round(percentage * self.size_of_bar))
        bars_left = self.size_of_bar - bars_completed
        return "[" + "#" * bars_completed + "-" * bars_left + "]"

    def time_left(self, counter):
        t1 = datetime.now()
        dt = (t1 - self.t0).total_seconds()
        seconds_left = dt / counter * (self.expected_size - counter)
        hours = floor(seconds_left / 3600)
        seconds_left -= hours * 3600
        mins = floor(seconds_left / 60)
        seconds_left -= mins * 60
        secs = round(seconds_left)
        return "%d:%02d:%02d" % (hours, mins, secs)

    def rate(self, counter):
        t1 = datetime.now()
        dt = (t1 - self.t0).total_seconds()
        rate = 8 * counter / dt
        return rate_str(rate)

    def progress(self, counter):
        # percentage = counter / self.expected_size * 100
        return (
            self.bar(counter)
            + " "
            + self.rate(counter)
            + ", ETA: "
            + self.time_left(counter)
            + ", "
            + size_str(counter)
            + " / "
            + size_str(self.expected_size)
        )

    def show_progress(self, counter):
        if counter >= self.last_report + self.report_every:
            msg = self.progress(counter)
            self.last_report = counter
            main_logger.debug(msg)

    def show_final_progress(self, counter):
        msg = self.progress(counter)
        self.last_report = counter
        main_logger.debug(msg)


def download_url(url, file_name, ca_file=CA_FILE, chunk_size=CHUNK_SIZE):
    index = 0
    r = requests.get(url, verify=ca_file, stream=True)
    total_length = int(r.headers.get("content-length"))
    t = transfer_bar(total_length)
    tmp_file_name = file_name + ".tmp"
    with open(tmp_file_name, "wb") as h:
        for ch in r.iter_content(chunk_size=chunk_size):
            if ch:
                h.write(ch)
                index += len(ch)
                t.show_progress(index)
    t.show_final_progress(index)

    if os.path.isfile(file_name):
        os.remove(file_name)
    os.rename(tmp_file_name, file_name)


def upload_url(url, filename, ca_file=CA_FILE, chunk_size=CHUNK_SIZE):
    headers = {}
    index = 0
    offset = 0
    # content_name = str(filename)
    content_path = os.path.abspath(filename)
    content_size = os.stat(content_path).st_size
    t = transfer_bar(content_size)

    with open(filename, "rb") as h:
        chunk = h.read(chunk_size)

        while chunk:
            offset = index + len(chunk)
            headers["Content-Type"] = "application/octet-stream"
            headers["Content-length"] = str(content_size)
            headers["Content-Range"] = "bytes %s-%s/%s" % (
                index,
                offset - 1,
                content_size,
            )

            index = offset
            # r = requests.put(url, data=chunk, headers=headers, verify=ca_file, stream=True)
            chunk = h.read(chunk_size)
            t.show_progress(offset)

    t.show_final_progress(offset)


def copy_file(source_file, dest_file, chunk_size=CHUNK_SIZE):
    # content_path = os.path.abspath(source_file)
    content_size = os.stat(source_file).st_size
    t = transfer_bar(content_size)

    source_f = open(source_file, "rb")
    dest_f = open(dest_file, "wb")
    bytes_read = 0

    chunk = source_f.read(chunk_size)
    while chunk:
        dest_f.write(chunk)
        bytes_read += len(chunk)
        t.show_progress(bytes_read)
        chunk = source_f.read(chunk_size)

    source_f.close()
    dest_f.close()


class Disk:
    def __init__(self, disk_info, disk_service, oh, chunk_size=CHUNK_SIZE):
        self.disk_info = disk_info
        self.disk_service = disk_service
        self.ca_file = oh.ca_file
        self.chunk_size = chunk_size
        self.oh = oh
        self.transfers_service = oh.transfers_service

    def id(self):
        return self.disk_info.id

    def image_id(self):
        return self.disk_info.image_id

    def name(self):
        return self.disk_info.name

    def __str__(self):
        return "Disk %s with id: %s" % (self.name(), self.image_id())

    def __repr__(self):
        return self.__str__()

    def provisioned_size(self):
        return self.disk_info.provisioned_size

    def status(self):
        return self.disk_info.status

    def description(self):
        return self.disk_info.description

    def is_sparse(self):
        return self.disk_info.sparse

    def format(self):
        return self.disk_info.format

    def actual_size(self):
        return self.disk_info.actual_size

    def initial_size(self):
        return self.disk_info.initial_size

    def interface(self):
        return self.disk_info.interface

    def total_size(self):
        return self.disk_info.total_size

    def information(self):
        return {
            "id": self.id(),
            "image_id": self.image_id(),
            "name": self.name(),
            "status": self.status(),
            "description": self.description(),
            "is_sparse": self.is_sparse(),
            "format": self.format(),
            "provisioned_size": self.provisioned_size(),
            "actual_size": self.actual_size(),
            "initial_size": self.initial_size(),
            "interface": self.interface(),
            "total_size": self.total_size(),
        }

    def upload(self, filename):
        # content_path = os.path.abspath(filename)
        # size = os.stat(content_path).st_size
        transfers_service = self.transfers_service
        transfer = transfers_service.add(
            types.ImageTransfer(
                disk=types.Disk(id=self.id()),
                direction=types.ImageTransferDirection.UPLOAD,
            )
        )

        transfer_service = transfers_service.image_transfer_service(transfer.id)
        while transfer.phase == types.ImageTransferPhase.INITIALIZING:
            time.sleep(3)
            transfer = transfer_service.get()

        #        client.upload(filename, transfer.transfer_url, self.ca_file)
        upload_url(
            transfer.transfer_url,
            filename,
            ca_file=self.ca_file,
            chunk_size=self.chunk_size,
        )

        transfer_service.finalize()


class SnapshotDisk(Disk):
    def __init__(
        self,
        disk_info,
        disk_service,
        oh,
        chunk_size=CHUNK_SIZE,
    ):
        self.disk_info = disk_info
        self.disk_service = disk_service
        self.oh = oh
        self.transfers_service = oh.transfers_service
        self.ca_file = oh.ca_file
        self.chunk_size = chunk_size

    def __str__(self):
        return "Snapshot disk %s with id: %s" % (self.name(), self.image_id())

    def download(self, download_dir=DOWNLOAD_DIRECTORY):
        transfer = self.transfers_service.add(
            types.ImageTransfer(
                snapshot=types.DiskSnapshot(id=self.image_id()),
                direction=types.ImageTransferDirection.DOWNLOAD,
            )
        )

        transfer_service = self.transfers_service.image_transfer_service(transfer.id)
        while transfer.phase == types.ImageTransferPhase.INITIALIZING:
            time.sleep(3)
            transfer = transfer_service.get()

        # Download virtual disk to qcow2 image:
        file_name = os.path.join(download_dir, self.image_id())
        download_url(
            transfer.transfer_url,
            file_name,
            ca_file=self.ca_file,
            chunk_size=self.chunk_size,
        )

        transfer_service.finalize()

    def upload(self, filename):
        # content_path = os.path.abspath(filename)
        # size = os.stat(content_path).st_size
        transfers_service = self.transfers_service
        transfer = transfers_service.add(
            types.ImageTransfer(
                snapshot=types.DiskSnapshot(id=self.image_id()),
                direction=types.ImageTransferDirection.UPLOAD,
            )
        )

        transfer_service = transfers_service.image_transfer_service(transfer.id)
        while transfer.phase == types.ImageTransferPhase.INITIALIZING:
            time.sleep(3)
            transfer = transfer_service.get()

        upload_url(
            transfer.transfer_url,
            filename,
            ca_file=self.ca_file,
            chunk_size=self.chunk_size,
        )
        transfer_service.finalize()

    def status(self):
        disk_info = self.disk_service.get()
        return disk_info.status


class Snapshot:
    def __init__(self, snapshot_info, snapshot_service, oh):
        self.snapshot_info = snapshot_info
        self.snapshot_service = snapshot_service
        self.oh = oh
        self.disks_service = snapshot_service.disks_service()

    def id(self):
        return self.snapshot_info.id

    def description(self):
        return self.snapshot_info.description

    def __str__(self):
        return "Snapshot %s with id: %s" % (self.description(), self.id())

    def __repr__(self):
        return self.__str__()

    def all_disks(self):
        disks = self.disks_service.list()
        all_disks = []

        for disk_info in disks:
            disk_service = self.disks_service.disk_service(disk_info.id)
            all_disks.append(SnapshotDisk(disk_info, disk_service, self.oh))

        return all_disks

    def download_disks(self, download_dir=DOWNLOAD_DIRECTORY):
        for disk in self.all_disks():
            main_logger.info("Downloading disk %s with image id %s" % (disk.id(), disk.image_id()))
            disk.download(download_dir=download_dir)

    def date(self):
        return self.snapshot_info.date

    def type(self):
        return self.snapshot_info.snapshot_type

    def remove(self):
        self.snapshot_service.remove()

    def all_disks_ok(self):
        for disk in self.all_disks():
            if disk.status() != types.DiskStatus.OK:
                return False
        return True

    def wait_for_all_disks_ok(self):
        while True:
            if self.all_disks_ok():
                break
            time.sleep(3)


class VM:
    def __init__(self, vm_info, vm_service, oh):
        self.vm_info = vm_info
        self.vm_service = vm_service
        self.oh = oh
        self.snapshots_service = vm_service.snapshots_service()
        self.disks_service = vm_service.disk_attachments_service()

    def all_snapshots(self, omit_active=True):
        snapshots = self.snapshots_service.list(all_content=True)
        all_snapshots = []

        for snapshot_info in snapshots:
            snapshot_service = self.snapshots_service.snapshot_service(snapshot_info.id)
            all_snapshots.append(Snapshot(snapshot_info, snapshot_service, self.oh))

        if omit_active:
            all_snapshots = [x for x in all_snapshots if x.type() != types.SnapshotType("active")]

        return sorted(all_snapshots, key=lambda x: x.date())

    def id(self):
        return self.vm_info.id

    def name(self):
        return self.vm_info.name

    def __str__(self):
        return "VM %s" % self.name()

    def __repr__(self):
        return self.__str__()

    def download_snapshot_disks(self, snapshot_name, download_dir=DOWNLOAD_DIRECTORY):
        main_logger.info("Downloading vm disks for selected snapshot for vm %s..." % self.name())
        for snap in self.all_snapshots():
            if snap.description() == snapshot_name:
                main_logger.info(
                    "-snapshot description %s, with id: %s" % (snap.description(), snap.id())
                )
                snap.download_disks(download_dir=download_dir)

    def add_disk(
        self,
        disk_name=NEW_DISK_NAME,
        description=NEW_DESCRIPTION,
        format=NEW_FORMAT,
        sparse=NEW_SPARSE,
        bootable=NEW_BOOTABLE,
        domain_name=NEW_STORAGE_DOMAIN_NAME,
        initial_size=None,
        provisioned_size=None,
        id=None,
    ):
        disk_info = types.Disk(
            name=disk_name,
            description=description,
            format=format,
            sparse=sparse,
            provisioned_size=provisioned_size,
            initial_size=initial_size,
            id=id,
            storage_domains=[types.StorageDomain(name=domain_name)],
        )

        disk_attachment = self.disks_service.add(
            types.DiskAttachment(
                disk=disk_info,
                interface=types.DiskInterface.VIRTIO_SCSI,
                bootable=bootable,
                active=True,
            )
        )

        disk_service = self.oh.disks_service.disk_service(disk_attachment.id)
        while True:
            time.sleep(5)
            disk1 = disk_service.get()
            if disk1.status == types.DiskStatus.OK:
                break

        disk_info = disk_service.get()
        return Disk(disk_info, disk_service, self.oh)

    def settings(self):
        vm_info = self.vm_info
        settings = {
            "name": vm_info.name,
            "id": vm_info.id,
            "cpu_architecture": vm_info.cpu.architecture,
            "memory": vm_info.memory,
        }

        settings["snapshot_sequence"] = []

        main_logger.info("Reading snapshots for vm %s" % self.name())
        settings["disk_info"] = {}

        for snapshot in self.all_snapshots(omit_active=True):
            element = {"id": snapshot.id(), "description": snapshot.description()}
            main_logger.debug(
                "Discovered snapshot with id %s and description %s"
                % (snapshot.id(), snapshot.description())
            )
            settings["snapshot_sequence"].append(element)

            for disk in snapshot.all_disks():
                settings["disk_info"][disk.image_id()] = disk.information()

                main_logger.debug("Discovered disk with:")
                main_logger.debug(" -image id: %s" % disk.image_id())
                main_logger.debug(" -disk id: %s" % disk.id())
                main_logger.debug(" -actual size: %s" % disk.actual_size())
                main_logger.debug(" -provisioned size: %s" % disk.provisioned_size())

        return settings

    def save_settings(self, save_dir=SAVE_DIRECTORY, filename=None):
        if filename is None:
            filename = self.vm_info.name + ".pickle"

        full_filename = os.path.join(save_dir, filename)
        with open(full_filename, "wb") as f:
            vm_info = self.settings()
            pickle.dump(vm_info, f)

    def add_snapshot(self, description="", disk_attachments=[]):
        if len(disk_attachments) == 0:
            snapshot = self.snapshots_service.add(
                types.Snapshot(description=description, persist_memorystate=False)
            )
        else:
            snapshot = self.snapshots_service.add(
                types.Snapshot(
                    description=description,
                    disk_attachments=disk_attachments,
                    persist_memorystate=False,
                ),
            )

        # Waiting for Snapshot creation to finish
        snapshot_service = self.snapshots_service.snapshot_service(snapshot.id)
        while True:
            time.sleep(3)
            snapshot = snapshot_service.get()
            if snapshot.snapshot_status == types.SnapshotStatus.OK:
                break

    def get_snapshot_by_description(self, description):
        snapshots = self.snapshots_service.list()
        for snapshot_info in snapshots:
            if snapshot_info.description == description:
                snapshot_service = self.snapshots_service.snapshot_service(snapshot_info.id)
                return Snapshot(snapshot_info, snapshot_service, self.oh)

    def status(self):
        return self.vm_service.get().status

    def remove_snapshot(self, description):
        snap = self.get_snapshot_by_description(description)
        if not snap:
            return
        snap.wait_for_all_disks_ok()
        snap.remove()
        while True:
            snap2 = self.get_snapshot_by_description(description)
            if not snap2:
                break
            time.sleep(3)

    def add_base_disk(self, base_disk, storage_domain=STORAGE_DOMAIN):
        new_disk = self.add_disk(
            disk_name=base_disk["name"],
            description=base_disk["description"],
            format=base_disk["format"],
            sparse=base_disk["is_sparse"],
            provisioned_size=base_disk["provisioned_size"],
            initial_size=base_disk["actual_size"],
            domain_name=storage_domain,
            bootable=False,
        )
        return new_disk

    def generic_disk_attachment(self, new_disk):
        return types.DiskAttachment(disk=types.Disk(id=new_disk.id()))

    def non_base_disk_attachment(self, non_base_disk, new_disk_id):
        actual_size = non_base_disk["actual_size"]
        provisioned_size = non_base_disk["provisioned_size"]
        return types.DiskAttachment(
            disk=types.Disk(
                id=new_disk_id,
                name=non_base_disk["name"],
                actual_size=actual_size,
                provisioned_size=provisioned_size,
                format=non_base_disk["format"],
                sparse=non_base_disk["is_sparse"],
            )
        )

    def add_disk_snapshots2(self, settings, storage_domain=STORAGE_DOMAIN):
        chains = settings["chains"]
        self.disk_mappings = {}
        disk_dict = settings["disk_info"]

        # make sure actual size of snapshot does not exceed the base image size

        for snapshot in settings["snapshot_sequence"]:
            s_id = snapshot["id"]
            description = snapshot["description"]
            main_logger.debug("debug", "Snapshot %s, description: %s" % (s_id, description))
            disk_attachments = []
            base_disks = []
            non_base_disks = []
            # get disks that correspond to this snapshot
            for key, chain in chains.items():
                matching_disks = [x for x in chain if x["snapshot_id"] == s_id]
                for matching_disk in matching_disks:
                    image_id = matching_disk["image_id"]
                    disk_id = matching_disk["disk_id"]
                    disk_info = disk_dict[image_id]

                    if matching_disk == chain[0]:
                        # this is a base image
                        # provisioned_size = max([x['actual_size'] for x in chain])
                        # disk_info['provisioned_size'] = provisioned_size
                        base_disks.append(disk_info)

                    else:
                        # this is not a base disk.
                        non_base_disks.append(disk_info)

            # We need to attach every base disk to the current VM state
            for base_disk in base_disks:
                new_disk = self.add_base_disk(base_disk, storage_domain=storage_domain)
                self.disk_mappings[base_disk["id"]] = new_disk.id()
                main_logger.debug(
                    "Included base image with id: %s as a new disk with id %s (image id:%s) in"
                    " snapshot attachments" % (base_disk["id"], new_disk.id(), new_disk.image_id())
                )

                disk_attachments.append(self.generic_disk_attachment(new_disk))

            for non_base_disk in non_base_disks:
                image_id = non_base_disk["image_id"]
                disk_id = non_base_disk["id"]
                new_disk_id = self.disk_mappings[disk_id]
                main_logger.debug("Adding non-base image with id: %s as a new image" % image_id)
                disk_attachments.append(self.non_base_disk_attachment(non_base_disk, new_disk_id))

            self.add_snapshot(description=description, disk_attachments=disk_attachments)


def image_mappings(old_chains, new_chains, disk_mappings):
    mappings = {}
    for old_disk_id, old_chain in old_chains.items():
        new_disk_id = disk_mappings[old_disk_id]
        new_chain = new_chains[new_disk_id]
        for i, old_element in enumerate(old_chain):
            old_image_id = old_element["image_id"]
            new_element = new_chain[i]
            new_image_id = new_element["image_id"]
            mappings[new_image_id] = old_image_id

    return mappings


def reverse_mappings(mappings):
    r_mappings = {}
    for new_disk_id, old_disk_id in mappings.items():
        r_mappings[old_disk_id] = new_disk_id
    return r_mappings


def qemu_info(filename):
    s = subprocess.check_output(["qemu-img", "info", filename, "--output=json"])
    return json.loads(s)


def qemu_rebase(filename, new_base, format):
    s = subprocess.check_output(
        ["qemu-img", "rebase", "-u", filename, "-b", new_base, "-F", format]
    )
    return s


def qemu_commit(filename):
    s = subprocess.check_output(["qemu-img", "commit", filename])
    return s


def qemu_info_dir(directory, filenames="*"):
    q = {}
    if filenames == "*":
        path = os.path.join(directory, "*")
        filenames = glob.glob(path)

    for filename in filenames:
        key = os.path.basename(filename)
        if "." not in key:
            q[key] = qemu_info(filename)

    return q


def qemu_chains(directory, filenames="*"):
    disks = qemu_info_dir(directory, filenames=filenames)

    # build chain
    depths = {}
    for disk_name in disks:
        d = disks[disk_name]
        l = 0
        ancestor_name = disk_name
        while "backing-filename" in d:
            ancestor_name = d["backing-filename"]
            d = disks[ancestor_name]
            l += 1

        depths[disk_name] = {"depth": l, "ancestor": ancestor_name}

    ancestors = []
    for disk_id, element in depths.items():
        if element["ancestor"] not in ancestors:
            ancestors.append(element["ancestor"])

    lengths = {}
    children = []
    chains = {}
    for ancestor in ancestors:
        l = [x["depth"] for k, x in depths.items() if x["ancestor"] == ancestor]
        lengths[ancestor] = max(l)
        chains[ancestor] = [None] * (lengths[ancestor] + 1)
        child_list = [
            k for k, x in depths.items() if (x["ancestor"] == ancestor) and (x["depth"] == max(l))
        ]
        children.append(child_list[0])

    for disk_id, _ in depths.items():
        ancestor = [x["ancestor"] for k, x in depths.items() if k == disk_id][0]
        index = [x["depth"] for k, x in depths.items() if k == disk_id][0]
        chains[ancestor][index] = disk_id

    return chains


def commit_chains(directory=SAVE_DIRECTORY):
    main_logger.info("Beginning commits of disk chains in directory %s" % directory)
    chains = qemu_chains(directory)
    main_logger.info("Chain information:")
    for chain in chains:
        main_logger.debug(chain)

    for _, chain in chains.items():
        for _, disk in enumerate(reversed(chain)):
            if disk != chain[0]:
                filename = os.path.join(directory, disk)
                main_logger.debug("Committing %s" % filename)
                qemu_commit(filename)
                main_logger.debug("Commited %s" % filename)

    return chains


class OvirtHandler:
    def __init__(
        self,
        url=URL,
        username=USERNAME,
        password=PASSWORD,
        ca_file=CA_FILE,
        download_dir=DOWNLOAD_DIRECTORY,
        chunk_size=CHUNK_SIZE,
    ):
        self.connection = sdk.Connection(
            url=url, username=username, ca_file=ca_file, password=password
        )

        self.system_service = self.connection.system_service()
        self.dmeineisks_service = self.system_service.disks_service()
        self.vms_service = self.system_service.vms_service()
        self.transfers_service = self.system_service.image_transfers_service()
        self.storage_domains_service = self.system_service.storage_domains_service()
        self.ca_file = ca_file

    def terminate_with_error(self, msg, exc=None):
        main_logger.error(msg + ". Terminating.")
        if exc:
            main_logger.debug(exc, exc_info=True)
            raise exc

    def get_vms(self, query=None):
        if query is None:
            vms = self.vms_service.list(all_content=True)
        else:
            vms = self.vms_service.list(search=query, all_content=True)

        vm_objects = []

        for vm in vms:
            vm_service = self.vms_service.vm_service(vm.id)
            vm_objects.append(VM(vm, vm_service, self))

        return vm_objects

    def get_vm_by_name(self, vm_name):
        query = "name=%s" % vm_name
        vms = self.get_vms(query=query)
        if len(vms) == 0:
            return None
        else:
            return vms[0]

    def vm_settings_from_file(self, vm_name, save_dir=SAVE_DIRECTORY):
        full_filename = os.path.join(save_dir, vm_name)
        with open(full_filename + ".pickle", "rb") as f:
            vm_info = pickle.load(f)

        return vm_info

    def add_empty_vm(self, vm_name, cluster_name=RECOVERY_CLUSTER, template=RECOVERY_TEMPLATE):
        vm_info = self.vms_service.add(
            vm=types.Vm(
                name=vm_name,
                cluster=types.Cluster(name=cluster_name),
                template=types.Template(name=template),
            )
        )

        vm_service = self.vms_service.vm_service(vm_info.id)

        return VM(vm_info, vm_service, self.connection)

    def add_vm_from_settings(
        self,
        settings,
        storage_domain=STORAGE_DOMAIN,
        template=RECOVERY_TEMPLATE,
        cluster_name=RECOVERY_CLUSTER,
        directory=DOWNLOAD_DIRECTORY,
        commit=True,
    ):
        # Create empty vm
        vm_info = self.vms_service.add(
            vm=types.Vm(
                name=settings["name"],
                cluster=types.Cluster(name=cluster_name),
                template=types.Template(name=template),
                memory=settings["memory"],
            )
        )

        vm_service = self.vms_service.vm_service(vm_info.id)
        vm = VM(vm_info, vm_service, self)

        main_logger.info("Attempting chain commit")
        chains = commit_chains(directory=directory)
        for base_image_id in chains:
            filename = os.path.join(directory, base_image_id)
            # disk_info = qemu_info(filename)
            base_disk = settings["disk_info"][base_image_id]
            new_disk = vm.add_base_disk(base_disk, storage_domain=storage_domain)
            main_logger.info("Uploading %s" % filename)
            new_disk.upload(filename)

        return vm

    def finalize_all_transfers(self):
        transfers = self.transfers_service.list()
        for transfer in transfers:
            transfer_service = self.transfers_service.image_transfer_service(transfer.id)
            transfer_service.finalize()


# oh = ovirt_handler()
# vm = oh.get_vm_by_name('test_for_backup')
# vm.save_settings()
# vm.download_snapshot_disks()
# settings = oh.vm_settings_from_file('test_for_backup')
# settings['name'] = 'test_for_restore'
# vm = oh.add_vm_from_settings(settings)
