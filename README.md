## _Ovirtsavior_: a tool for backing-up your Ovirt VMs to local (or remote) storage

## About

_Ovirtsavior_ is an old fashioned backup/restore tool for the Ovirt virtualization platform. It can be used to:

- download the disks of a running VM to your local file system (backup mode).
- upload the disks of a previously backed up VM to a new Ovirt VM (restore mode).

You can use `rclone` to mount a remote directory to your local storage (Google Drive, etc). Use this with caution, it is probably much safer to save it to a local disc or NFS mount.

_Ovirtsavior_ was created due to the support of the [Study in Greece](https://studyingreece.edu.gr/) initiative. 

## Disclaimer

Use the software **at your own risk**. This has not been heavily tested in production environments. We suggest running the scripts on Ubuntu 20.04. It should work on Ovirt 4.3 and 4.4 engines.

## VM backup & restore

The tool is used to backup and restore the discs. 
- During the backup phase, the discs are downloaded in the working directory along with some rudimentary information regarding the VM parameters. 
- In the restore phase, the disks are uploaded to the VM. You need to adjust the VM parameters including memory, CPU cores and also designate _bootable_ disks.  

Maybe in a future version we will include this as well. 

Some information on the original VM are saved as well in a pickle file inside the VM folder. 

## Snapshots
All snapshot disks are downloaded during the backup phase in the working directory. However, during the restore phase, the disks are copied to a local temporary directory and the snapshots are collapsed. This practically means that you do not get any previous snapshots in the restored VMs. The reason for this is that we found there may be a bug in the Ovirt API not allowing you to upload intermittent disks with provisioned size lower than the actual size which prevented us from restoring the full snapshot chain. We plan to revisit this in the future.  

## Prequisites

You need to install `qemu-img` and `python3-ovirt-engine-sdk4`

On Ubuntu 20.04
```bash
apt update
apt-get --assume-yes install gcc \
    libxml2-dev \ 
    python3-dev \
    python3-pip \
    libcurl4-openssl-dev \
    libssl-dev \
    qemu-utils
pip3 install ovirt-engine-sdk-python
```
## Clone repo
```bash
git clone https://gitlab.hua.gr/thkam/ovirtbackup
```

if for some reason you get a `server certificate verification failed. CAfile: none CRLfile: none` try to issue:
```bash
export GIT_SSL_NO_VERIFY=1
```

## Modes
There are two modes you can use the `ovirt-savior` script: backup and restore. In backup mode, you download the VM disks to a local or remote folder. In restore mode, you create a VM using the OVirt API and then upload the discs. You have the option of collapsing the original VM's snapshots on the restored VM and this is probably your safest bet on getting the VM to work again. There seems to be a bug in OVirt that extends the snapshot disc image beyond its maximum size and although this does not affect the original running VM, it prohibits uploading the discs to the restored VMs.

## Backup flow
The script creates a temporary snapshot of a running or powered off VM, downloads the disk chains contained in this snapshot and then removes the snapshot

## Restore flow
A new VM is created with the same or different name and the disks are uploaded. 

## Usage
```
python3 ovirt-savior.py [mode] -s [config-file]
```
It may be a good idea to run this as root when using NFS shares. The `mode` option can be either `backup` or `restore`. The `config-file` specifies a configuration file that contains several options.

### Sample configuration file
This is a sample configuration file that can be used for `config-file`

```
[CONNECTION]
ca_file : elovirt-pki-resource.cer
username : admin@internal
password : yourpassword
ovirt_url : https://elovirtengine.example.com/ovirt-engine/api

[RESTORATION]
storage_domain : vm_storage
cluster_name : Default
template : Blank

[DIRECTORIES]
local_directory : /tmp
working_directory : /backup

[SNAPSHOT]
backup_snapshot_description : SAVIOR_BACKUP_SNAPSHOT

[TRANSFER]
chunk_size : 1048576

[VM]
vm_name : test_for_backup
new_vm_name : test_for_restore

[MAIL]
smtp_sender : ovirt@example.com
smtp_password : mailpassword
smtp_server : mail.example.com
smtp_port : 587
smtp_recipient : admins@example.com

``` 
The following is a brief explanation of the parameters:

#### Connection section
- `ca_file`: the Ovirt engine certificate file. It can be downloaded on the OVirt engine web page.
- `username`: an Ovirt superadmin account username. You must include the domain (e.g. admin@internal).
- `password`: the password of the above account.
- `ovirt_url`: the URL of the Ovirt engine API, should be something in the line of https://elovirtengine.hua.gr/ovirt-engine/api

#### Directories section
- `working_directory`: the directory where the backups will be stored. The script creates a directory for each VM under `working_directory`
- `local_directory`: a directory where the discs are copied before uploaded to Ovirt. Use this in conjunction with `copy_to_local` and `commit` parameters in the restoration section. 

#### Restoration section
This section describes the parameters used during the restoration process.
- `storage_domain`: the name of the storage domain where the VM will be restored (e.g. `vm_storage`).
- `cluster_name` : the name of the cluster where the VM will be restored. (e.g. `Default`)
- `template` : you could use this to define a template where the VM is based but since it is restored you should leave it equal to `Blank`.
- `commit` : if this is set to `yes` then all snapshot on the disks are collapsed on the restored VM. This is the preferred mode of operation to save disk space and avoid certain bugs of the Ovirt disk provisioning.
- `copy_to_local` : if this is set to `yes` the disks are first copied to the `local_directory` before the restoration. Use this especially if you set the `commit` option to `yes` because otherwise the snapshots will collapse on the working backup folder.

#### Snapshot section
- `backup_snapshot_description` is the name of a temporary snapshot used to backup VMs (e.g. SAVIOR_BACKUP_SNAPSHOT)

#### Transfer section
- `chunk_size` : size of the blocks to be used in the disk transfers in bytes. Usually `1048576` is adequate.

#### Remote section
- `mount_remote`: if set to `yes` then a remote will be mounted using `rclone`.
- `rclone_remote`: is the name of the remote to be mounted before the backup or restore operation takes place. It will be unmounted once it is done.

#### VM section:
- `vm_name` : the name of the VM to be backed up or restored.
- `new_vm_name` : the new name for the restored VM. It can be different than `vm_name`.

#### Mail section
- `smtp_sender` : Account sending the job notifications
- `smtp_password` : Account password
- `smtp_server` : E-mail server address
- `smtp_port` : Port for communication (e.g. 587)
- `smtp_recipient` : Address receiving email notifications


## Logging
The file `savior.log` located on the same folder as the script, contains a log of current backup and restore jobs. This log is send by e-mail according to the settings of the `[MAIL]` section.

## Backup on NFS share
One common scenario is when you wish to backup your vm disks on an NFS share. On the NFS remote server you need to install:
```bash
apt update
apt install nfs-kernel-server
mkdir /var/nfs/backup -p
chown nobody:nogroup /var/nfs/backup
```

Create the file `/etc/exports`
```
/var/nfs/backup		10.100.59.154(rw,sync,no_subtree_check)
```

Then do:
```
ufw allow nfs
systemctl restart nfs-kernel-server
```

On the NFS client which will execute the ovirt backup scripts you need to install:
```bash
apt update
apt install nfs-common
sudo mount 195.130.90.2:/var/nfs/backup /nfs/backup
df -h
```

To mount the folder at boot append the following line to `\etc\fstab`
```
195.130.90.2:/var/nfs/backup    /nfs/backup     nfs  auto,nofail,noatime,nolock,intr,tcp,actimeo=1800 0 0
```





