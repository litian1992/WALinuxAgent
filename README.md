
# Microsoft Azure Linux Agent

## Linux distributions support

The list of distros we officially support is maintained at: [Linux distributions supported by Azure](https://docs.microsoft.com/en-us/azure/virtual-machines/linux/endorsed-distros). Our daily automation tests most of these distributions. The Agent can be
used on other distributions as well, but development, testing and support for those are done by the open source community. This repo contains community-driven support for some distributions which are not officially supported by Azure.

Testing is done using the develop branch, which can be unstable. For a stable build please use the master branch instead.

[![CodeCov](https://codecov.io/gh/Azure/WALinuxAgent/branch/develop/graph/badge.svg)](https://codecov.io/gh/Azure/WALinuxAgent/branch/develop)


## Introduction

The Microsoft Azure Linux Agent (waagent) manages Linux provisioning and VM interaction with the Azure Fabric Controller. It provides the following
functionality for Linux IaaS deployments:

* Image Provisioning
  * Creation of a user account
  * Configuring SSH authentication types
  * Deployment of SSH public keys and key pairs
  * Setting the host name
  * Publishing the host name to the platform DNS
  * Reporting SSH host key fingerprint to the platform
  * Resource Disk Management
  * Formatting and mounting the resource disk
  * Configuring swap space

* Networking
  * Manages routes to improve compatibility with platform DHCP servers
  * Ensures the stability of the network interface name

* Kernel
  * Configure virtual NUMA (disable for kernel <2.6.37)
  * Configure SCSI timeouts for the root device (which could be remote)

* Diagnostics
  * Console redirection to the serial port

* SCVMM Deployments
  * Detect and bootstrap the VMM agent for Linux when running in a System
    Center Virtual Machine Manager 2012R2 environment

* VM Extension
  * Inject component authored by Microsoft and Partners into Linux VM (IaaS)
    to enable software and configuration automation
  * VM Extension reference implementation on [GitHub](https://github.com/Azure/azure-linux-extensions)

## Communication

The information flow from the platform to the agent occurs via two channels:

* A boot-time attached DVD for IaaS deployments.
  This DVD includes an OVF-compliant configuration file that includes all
  provisioning information other than the actual SSH keypairs.

* A TCP endpoint exposing a REST API used to obtain deployment and topology
  configuration.

### HTTP Proxy
The Agent will use an HTTP proxy if provided via the `http_proxy` (for `http` requests) or
`https_proxy` (for `https` requests) environment variables. Due to limitations of Python, 
the agent *does not* support HTTP proxies requiring authentication. 

Similarly, the Agent will bypass the proxy if the environment variable `no_proxy` is set.

Note that the way to define those environment variables for the Agent service varies across different distros. For distros
that use systemd, a common approach is to use Environment or EnvironmentFile in the [Service] section of the service 
definition, for example using an override or a drop-in file (see "systemctl edit" for overrides).

Example
```bash
    # cat /etc/systemd/system/walinuxagent.service.d/http-proxy.conf
    [Service]
    Environment="http_proxy=http://proxy.example.com:80/"
    Environment="https_proxy=http://proxy.example.com:80/"
    #
```

The Agent passes its environment to the VM Extensions it executes, including `http_proxy` and `https_proxy`, so defining
a proxy for the Agent will also define it for the VM Extensions.


The [`HttpProxy.Host` and `HttpProxy.Port`](#httpproxyhost-httpproxyport) configuration variables, if used, override 
the environment settings. Note that this configuration variables are local to the Agent process and are not passed to
VM Extensions.

## Requirements

The following systems have been tested and are known to work with the Azure
Linux Agent.  Please note that this list may differ from the official list
of supported systems on the Microsoft Azure Platform as described [here](https://docs.microsoft.com/en-us/azure/virtual-machines/linux/endorsed-distros).

Waagent depends on some system packages in order to function properly:

* Python 2.6+
* OpenSSL 1.0+
* OpenSSH 5.3+
* Filesystem utilities: sfdisk, fdisk, mkfs, parted
* Password tools: chpasswd, sudo
* Text processing tools: sed, grep
* Network tools: ip-route, iptables

## Installation

Installing via your distribution's package repository is the only method that is supported.

You can install from source for more advanced options, such as installing to a custom location or creating 
custom images. Installing from source, though, may override customizations done to the Agent by your 
distribution, and is meant only for advanced users. We provide very limited support for this method.

To install from source, you can use **setuptools**:

```bash
    sudo python setup.py install --register-service
```

For Python 3, use:

```bash
    sudo python3 setup.py install --register-service
```

You can view more installation options by running:

```bash
    sudo python setup.py install --help
```

The agent's log file is kept at `/var/log/waagent.log`.

Lastly, you can also customize your own RPM or DEB packages using the configuration
samples provided in the deb and rpm sections below. This method is also meant for advanced users and we
provide very limited support for it.


## Upgrade

Upgrading via your distribution's package repository or using automatic updates are the only supported
methods. More information can be found here: [Update Linux Agent](https://learn.microsoft.com/en-us/azure/virtual-machines/extensions/update-linux-agent) 

To upgrade the Agent from source, you can use **setuptools**. Upgrading from source is meant for advanced 
users and we provide very limited support for it.

```bash
    sudo python setup.py install --force
```

Restart waagent service,for most of linux distributions:

```bash
    sudo service waagent restart
```

For Ubuntu, use:

```bash
    sudo service walinuxagent restart
```

For CoreOS, use:

```bash
    sudo systemctl restart waagent
```

## Command line options

### Flags

`-verbose`: Increase verbosity of specified command

`-force`: Skip interactive confirmation for some commands

### Commands

`-help`: Lists the supported commands and flags.

`-deprovision`: Attempt to clean the system and make it suitable for re-provisioning, by deleting the following:

* All SSH host keys (if Provisioning.RegenerateSshHostKeyPair is 'y' in the configuration file)
* Nameserver configuration in /etc/resolv.conf
* Root password from /etc/shadow (if Provisioning.DeleteRootPassword is 'y' in the configuration file)
* Cached DHCP client leases
* Resets host name to localhost.localdomain

  **WARNING!** Deprovision does not guarantee that the image is cleared of all sensitive information and suitable for redistribution.

`-deprovision+user`: Performs everything under deprovision (above) and also deletes the last provisioned user account and associated data.

`-version`: Displays the version of waagent

`-serialconsole`: Configures GRUB to mark ttyS0 (the first serial port) as the boot console. This ensures that kernel bootup logs are sent to the serial port and made available for debugging.

`-daemon`: Run waagent as a daemon to manage interaction with the platform. This argument is specified to waagent in the waagent init script.

`-start`: Run waagent as a background process

`-collect-logs [-full]`: Runs the log collector utility that collects relevant agent logs for debugging and stores them in the agent folder on disk. Exact location will be shown when run. Use flag `-full` for more exhaustive log collection. 

## Configuration

A configuration file (/etc/waagent.conf) controls the actions of waagent. Blank lines and lines whose first character is a `#` are ignored (end-of-line comments are *not* supported).

A sample configuration file is shown below:

```yml
Extensions.Enabled=y
Extensions.GoalStatePeriod=6
Provisioning.Agent=auto
Provisioning.DeleteRootPassword=n
Provisioning.RegenerateSshHostKeyPair=y
Provisioning.SshHostKeyPairType=rsa
Provisioning.MonitorHostName=y
Provisioning.DecodeCustomData=n
Provisioning.ExecuteCustomData=n
Provisioning.PasswordCryptId=6
Provisioning.PasswordCryptSaltLength=10
ResourceDisk.Format=y
ResourceDisk.Filesystem=ext4
ResourceDisk.MountPoint=/mnt/resource
ResourceDisk.MountOptions=None
ResourceDisk.EnableSwap=n
ResourceDisk.EnableSwapEncryption=n
ResourceDisk.SwapSizeMB=0
Logs.Verbose=n
Logs.Collect=y
Logs.CollectPeriod=3600
OS.AllowHTTP=n
OS.RootDeviceScsiTimeout=300
OS.EnableFIPS=n
OS.OpensslPath=None
OS.SshClientAliveInterval=180
OS.SshDir=/etc/ssh
HttpProxy.Host=None
HttpProxy.Port=None
```

The various configuration options are described in detail below. Configuration
options are of three types : Boolean, String or Integer. The Boolean
configuration options can be specified as "y" or "n". The special keyword "None"
may be used for some string type configuration entries as detailed below.

### Configuration File Options

#### __Extensions.Enabled__

_Type: Boolean_  
_Default: y_

This allows the user to enable or disable the extension handling functionality in the
agent. Valid values are "y" or "n". If extension handling is disabled, the goal state 
will still be processed and VM status is still reported, but only every 5 minutes. 
Extension config within the goal state will be ignored. Note that functionality such
as password reset, ssh key updates and backups depend on extensions. Only disable this
if you do not need extensions at all.

_Note_: disabling extensions in this manner is not the same as running completely 
without the agent. In order to do that, the `provisionVMAgent` flag must be set at
provisioning time, via whichever API is being used. We will provide more details on
this on our wiki when it is generally available. 

#### __Extensions.WaitForCloudInit__

_Type: Boolean_  
_Default: n_

Waits for cloud-init to complete (cloud-init status --wait) before executing VM extensions.

Both cloud-init and VM extensions are common ways to customize a VM during initial deployment. By
default, the agent will start executing extensions while cloud-init may still be in the 'config' 
stage and won't wait for the 'final' stage to complete. Cloud-init and extensions may execute operations
that conflict with each other (for example, both of them may try to install packages). Setting this option
to 'y' ensures that VM extensions are executed only after cloud-init has completed all its stages.

Note that using this option requires creating a custom image with the value of this option set to 'y', in
order to ensure that the wait is performed during the initial deployment of the VM.

#### __Extensions.WaitForCloudInitTimeout__

_Type: Integer_  
_Default: 3600_

Timeout in seconds for the Agent to wait on cloud-init. If the timeout elapses, the Agent will continue 
executing VM extensions. See Extensions.WaitForCloudInit for more details. 

#### __Extensions.GoalStatePeriod__

_Type: Integer_  
_Default: 6_

How often to poll for new goal states (in seconds) and report the status of the VM
and extensions. Goal states describe the desired state of the extensions on the VM.

_Note_: setting up this parameter to more than a few minutes can make the state of
the VM be reported as unresponsive/unavailable on the Azure portal. Also, this 
setting affects how fast the agent starts executing extensions. 

#### __AutoUpdate.UpdateToLatestVersion__

_Type: Boolean_
_Default: y_

Enables auto-update of the Extension Handler. The Extension Handler is responsible
for managing extensions and reporting VM status. The core functionality of the agent
is contained in the Extension Handler, and we encourage users to enable this option
in order to maintain an up to date version.
 
When this option is enabled, the Agent will install new versions when they become
available. When disabled, the Agent will not install any new versions, but it will use
the most recent version already installed on the VM.

_Notes_:
1. This option was added on version 2.10.0.8 of the Agent. For previous versions, see AutoUpdate.Enabled.
2. If both options are specified in waagent.conf, AutoUpdate.UpdateToLatestVersion overrides the value set for AutoUpdate.Enabled.
3. Changing config option requires a service restart to pick up the updated setting.

For more information on the agent version, see our [FAQ](https://github.com/Azure/WALinuxAgent/wiki/FAQ#what-does-goal-state-agent-mean-in-waagent---version-output). <br/>
For more information on the agent update, see our [FAQ](https://github.com/Azure/WALinuxAgent/wiki/FAQ#how-auto-update-works-for-extension-handler). <br/>
For more information on the AutoUpdate.UpdateToLatestVersion vs AutoUpdate.Enabled, see our [FAQ](https://github.com/Azure/WALinuxAgent/wiki/FAQ#autoupdateenabled-vs-autoupdateupdatetolatestversion). <br/>

#### __AutoUpdate.Enabled__

_Type: Boolean_  
_Default: y_

Enables auto-update of the Extension Handler. This flag is supported for legacy reasons and we strongly recommend using AutoUpdate.UpdateToLatestVersion instead. 
The difference between these 2 flags is that, when set to 'n', AutoUpdate.Enabled will use the version of the Extension Handler that is pre-installed on the image, while AutoUpdate.UpdateToLatestVersion will use the most recent version that has already been installed on the VM (via auto-update).

On most distros the default value is 'y'.

#### __Provisioning.Agent__

_Type: String_
_Default: auto_

Choose which provisioning agent to use (or allow waagent to figure it out by
specifying "auto"). Possible options are "auto" (default), "waagent", "cloud-init",
or "disabled".

#### __Provisioning.Enabled__ (*removed in 2.2.45*)

_Type: Boolean_ 
_Default: y_

This allows the user to enable or disable the provisioning functionality in the
agent. Valid values are "y" or "n". If provisioning is disabled, SSH host and
user keys in the image are preserved and any configuration specified in the
Azure provisioning API is ignored.

_Note_: This configuration option has been removed and has no effect. waagent
now auto-detects cloud-init as a provisioning agent (with an option to override
with `Provisioning.Agent`).

#### __Provisioning.MonitorHostName__

_Type: Boolean_ 
_Default: n_

Monitor host name changes and publish changes via DHCP requests.

#### __Provisioning.MonitorHostNamePeriod__

_Type: Integer_ 
_Default: 30_

How often to monitor host name changes (in seconds). This setting is ignored if
MonitorHostName is not set.

#### __Provisioning.UseCloudInit__

_Type: Boolean_ 
_Default: n_

This options enables / disables support for provisioning by means of cloud-init.
When true ("y"), the agent will wait for cloud-init to complete before installing
extensions and processing the latest goal state. _Provisioning.Enabled_ must be
disabled ("n") for this option to have an effect. Setting _Provisioning.Enabled_ to
true ("y") overrides this option and runs the built-in agent provisioning code.

_Note_: This configuration option has been removed and has no effect. waagent
now auto-detects cloud-init as a provisioning agent (with an option to override
with `Provisioning.Agent`).

#### __Provisioning.DeleteRootPassword__  

_Type: Boolean_  
_Default: n_  

If set, the root password in the /etc/shadow file is erased during the
provisioning process.

#### __Provisioning.RegenerateSshHostKeyPair__

_Type: Boolean_  
_Default: y_

If set, all SSH host key pairs (ecdsa, dsa and rsa) are deleted during the
provisioning process from /etc/ssh/. And a single fresh key pair is generated.
The encryption type for the fresh key pair is configurable by the
Provisioning.SshHostKeyPairType entry. Please note that some distributions will
re-create SSH key pairs for any missing encryption types when the SSH daemon is
restarted (for example, upon a reboot).

#### __Provisioning.SshHostKeyPairType__

_Type: String_  
_Default: rsa_

This can be set to an encryption algorithm type that is supported by the SSH
daemon on the VM. The typically supported values are "rsa", "dsa" and "ecdsa".
Note that "putty.exe" on Windows does not support "ecdsa". So, if you intend to
use putty.exe on Windows to connect to a Linux deployment, please use "rsa" or
"dsa".

#### __Provisioning.MonitorHostName__

_Type: Boolean_  
_Default: y_

If set, waagent will monitor the Linux VM for hostname changes (as returned by
the "hostname" command) and automatically update the networking configuration in
the image to reflect the change. In order to push the name change to the DNS
servers, networking will be restarted in the VM. This will result in brief loss
of Internet connectivity.

#### __Provisioning.DecodeCustomData__

_Type: Boolean_  
_Default: n_

If set, waagent will decode CustomData from Base64.

#### __Provisioning.ExecuteCustomData__

_Type: Boolean_  
_Default: n_

If set, waagent will execute CustomData after provisioning.

#### __Provisioning.PasswordCryptId__

_Type: String_  
_Default: 6_

Algorithm used by crypt when generating password hash.  

* 1 - MD5
* 2a - Blowfish
* 5 - SHA-256
* 6 - SHA-512

#### __Provisioning.PasswordCryptSaltLength__

_Type: String_  
_Default: 10_

Length of random salt used when generating password hash.

#### __ResourceDisk.Format__

_Type: Boolean_  
_Default: y_

If set, the resource disk provided by the platform will be formatted and mounted by waagent if the filesystem type requested by the user in "ResourceDisk.Filesystem" is anything other than "ntfs". A single partition of
type Linux (83) will be made available on the disk. Note that this partition will not be formatted if it can be successfully mounted.

#### __ResourceDisk.Filesystem__

_Type: String_  
_Default: ext4_

This specifies the filesystem type for the resource disk. Supported values vary
by Linux distribution. If the string is X, then mkfs.X should be present on the
Linux image. SLES 11 images should typically use 'ext3'. BSD images should use
'ufs2' here.

#### __ResourceDisk.MountPoint__

_Type: String_  
_Default: /mnt/resource_

This specifies the path at which the resource disk is mounted.

#### __ResourceDisk.MountOptions__

_Type: String_  
_Default: None_

Specifies disk mount options to be passed to the mount -o command. This is a comma
separated list of values, ex. 'nodev,nosuid'. See mount(8) for details.

#### __ResourceDisk.EnableSwap__

_Type: Boolean_  
_Default: n_

If set, a swap file (/swapfile) is created on the resource disk and added to the
system swap space.

#### __ResourceDisk.EnableSwapEncryption__

_Type: Boolean_  
_Default: n_

If set, the swap file (/swapfile) is mounted as an encrypted filesystem (flag supported only on FreeBSD.)

#### __ResourceDisk.SwapSizeMB__

_Type: Integer_  
_Default: 0_

The size of the swap file in megabytes.

#### __Logs.Verbose__

_Type: Boolean_  
_Default: n_

If set, log verbosity is boosted. Waagent logs to /var/log/waagent.log and
leverages the system logrotate functionality to rotate logs.


#### __Logs.Collect__

_Type: Boolean_  
_Default: y_

If set, agent logs will be periodically collected and uploaded to a secure location for improved supportability.

NOTE: This feature relies on the agent's resource usage features (cgroups); this flag will not take effect on any distro not supported.

#### __Logs.CollectPeriod__

_Type: Integer_  
_Default: 3600_

This configures how frequently to collect and upload logs. Default is each hour.

NOTE: This only takes effect if the Logs.Collect option is enabled.

#### __OS.AllowHTTP__

_Type: Boolean_  
_Default: n_

If SSL support is not compiled into Python, the agent will fail all HTTPS requests.
You can set this option to 'y' to make the agent fall-back to HTTP, instead of failing the requests.

NOTE: Allowing HTTP may unintentionally expose secure data.

#### __OS.EnableRDMA__

_Type: Boolean_  
_Default: n_

If set, the agent will attempt to install and then load an RDMA kernel driver
that matches the version of the firmware on the underlying hardware.

#### __OS.EnableFIPS__

_Type: Boolean_  
_Default: n_

If set, the agent will emit into the environment "OPENSSL_FIPS=1" when executing
OpenSSL commands. This signals OpenSSL to use any installed FIPS-compliant libraries.
Note that the agent itself has no FIPS-specific code. _If no FIPS-compliant certificates are
installed, then enabling this option will cause all OpenSSL commands to fail._

#### __OS.EnableFirewall__

_Type: Boolean_  
_Default: n (set to 'y' in waagent.conf)_

Creates firewall rules to allow communication with the VM Host only by the Agent.  

#### __OS.MonitorDhcpClientRestartPeriod__

_Type: Integer_
_Default: 30_

The agent monitor restarts of the DHCP client and restores network rules when it happens. This
setting determines how often (in seconds) to monitor for restarts.

#### __OS.RootDeviceScsiTimeout__

_Type: Integer_  
_Default: 300_

This configures the SCSI timeout in seconds on the root device. If not set, the
system defaults are used.

#### __OS.RootDeviceScsiTimeoutPeriod__

_Type: Integer_  
_Default: 30_

How often to set the SCSI timeout on the root device (in seconds). This setting is
ignored if RootDeviceScsiTimeout is not set.

#### __OS.OpensslPath__

_Type: String_  
_Default: None_

This can be used to specify an alternate path for the openssl binary to use for
cryptographic operations.

#### __OS.RemovePersistentNetRulesPeriod__
_Type: Integer_  
_Default: 30_

How often to remove the udev rules for persistent network interface names (75-persistent-net-generator.rules
and /etc/udev/rules.d/70-persistent-net.rules) (in seconds) 

#### __OS.SshClientAliveInterval__

_Type: Integer_  
_Default: 180_

This values sets the number of seconds the agent uses for the SSH ClientAliveInterval configuration option.

#### __OS.SshDir__

_Type: String_  
_Default: `/etc/ssh`_

This option can be used to override the normal location of the SSH configuration
directory.

#### __HttpProxy.Host, HttpProxy.Port__

_Type: String_  
_Default: None_

If set, the agent will use this proxy server for HTTP/HTTPS requests. These values
*will* override the `http_proxy` or `https_proxy` environment variables. Lastly,
`HttpProxy.Host` is required (if to be used) and `HttpProxy.Port` is optional.

#### __CGroups.EnforceLimits__

_Type: Boolean_  
_Default: y_

If set, the agent will attempt to set cgroups limits for cpu and memory for the agent process itself
as well as extension processes. See the wiki for further details on this.

#### __CGroups.Excluded__

_Type: String_  
_Default: customscript,runcommand_

The list of extensions which will be excluded from cgroups limits. This should be comma separated. 

#### __Protocol.EndpointDiscovery__

_Type: String_  
_Default: dhcp_

Determines how the agent will discover the [WireServer endpoint](https://learn.microsoft.com/en-us/azure/virtual-network/what-is-ip-address-168-63-129-16). 
Agent will use DHCP by default to discover the WireServer endpoint, but if this setting is 'static' the agent will use the known WireServer address (168.63.129.16).
Possible options are "dhcp" (default) or "static".

### Telemetry

WALinuxAgent collects usage data and sends it to Microsoft to help improve our products and services. The data collected is used to track service health and
assist with Azure support requests. Data collected does not include any personally identifiable information. Read our [privacy statement](http://go.microsoft.com/fwlink/?LinkId=521839)
to learn more.

WALinuxAgent does not support disabling telemetry at this time. WALinuxAgent must be removed to disable telemetry collection. If you need this feature,
please open an issue in GitHub and explain your requirement.

### Appendix

We do not maintain packaging information in this repo but some samples are shown below as a reference. See the downstream distribution repositories for officially maintained packaging.

#### deb packages

The official Ubuntu WALinuxAgent package can be found [here](https://launchpad.net/ubuntu/+source/walinuxagent).

Run once:

1. Install required packages

   ```bash
   sudo apt-get -y install ubuntu-dev-tools pbuilder python-all debhelper
   ```

2. Create the pbuilder environment

   ```bash
   sudo pbuilder create --debootstrapopts --variant=buildd
   ```

3. Obtain `waagent.dsc` from a downstream package repo

To compile the package, from the top-most directory:

1. Build the source package

   ```bash
   dpkg-buildpackage -S
   ```

2. Build the package

   ```bash
   sudo pbuilder build waagent.dsc
   ```

3. Fetch the built package, usually from `/var/cache/pbuilder/result`

#### rpm packages

The instructions below describe how to build an rpm package.

1. Install setuptools

   ```bash
   curl https://bootstrap.pypa.io/ez_setup.py -o - | python
   ```

2. The following command will build the binary and source RPMs:

   ```bash
   python setup.py bdist_rpm
   ```

-----

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/). For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.
