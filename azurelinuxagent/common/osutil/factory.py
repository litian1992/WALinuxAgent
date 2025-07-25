# Copyright 2018 Microsoft Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Requires Python 2.6+ and Openssl 1.0+
#


import azurelinuxagent.common.logger as logger
from azurelinuxagent.common.version import DISTRO_NAME, DISTRO_CODE_NAME, DISTRO_VERSION, DISTRO_FULL_NAME
from azurelinuxagent.common.utils.distro_version import DistroVersion
from .alpine import AlpineOSUtil
from .arch import ArchUtil
from .bigip import BigIpOSUtil
from .clearlinux import ClearLinuxUtil
from .coreos import CoreOSUtil
from .chainguard import ChainguardOSUtil
from .debian import DebianOSBaseUtil, DebianOSModernUtil
from .default import DefaultOSUtil
from .devuan import DevuanOSUtil
from .freebsd import FreeBSDOSUtil
from .gaia import GaiaOSUtil
from .iosxe import IosxeOSUtil
from .mariner import MarinerOSUtil
from .nsbsd import NSBSDOSUtil
from .openbsd import OpenBSDOSUtil
from .openwrt import OpenWRTOSUtil
from .redhat import RedhatOSUtil, Redhat6xOSUtil, RedhatOSModernUtil
from .suse import SUSEOSUtil, SUSE11OSUtil
from .photonos import PhotonOSUtil
from .ubuntu import UbuntuOSUtil, Ubuntu12OSUtil, Ubuntu14OSUtil, \
    UbuntuSnappyOSUtil, Ubuntu16OSUtil, Ubuntu18OSUtil
from .fedora import FedoraOSUtil


def get_osutil(distro_name=DISTRO_NAME,
               distro_code_name=DISTRO_CODE_NAME,
               distro_version=DISTRO_VERSION,
               distro_full_name=DISTRO_FULL_NAME):

    # We are adding another layer of abstraction here since we want to be able to mock the final result of the
    # function call. Since the get_osutil function is imported in various places in our tests, we can't mock
    # it globally. Instead, we add _get_osutil function and mock it in the test base class, AgentTestCase.
    return _get_osutil(distro_name, distro_code_name, distro_version, distro_full_name)


def _get_osutil(distro_name, distro_code_name, distro_version, distro_full_name):

    if distro_name == "photonos":
        return PhotonOSUtil()

    if distro_name == "arch":
        return ArchUtil()

    if "Clear Linux" in distro_full_name:
        return ClearLinuxUtil()

    if distro_name == "ubuntu":
        ubuntu_version = DistroVersion(distro_version)
        if ubuntu_version in [DistroVersion("12.04"), DistroVersion("12.10")]:
            return Ubuntu12OSUtil()
        if ubuntu_version in [DistroVersion("14.04"), DistroVersion("14.10")]:
            return Ubuntu14OSUtil()
        if ubuntu_version in [DistroVersion('16.04'), DistroVersion('16.10'), DistroVersion('17.04')]:
            return Ubuntu16OSUtil()
        if DistroVersion('18.04') <= ubuntu_version <= DistroVersion('24.04'):
            return Ubuntu18OSUtil()
        if distro_full_name == "Snappy Ubuntu Core":
            return UbuntuSnappyOSUtil()

        return UbuntuOSUtil()

    if distro_name in ("alpine", "alpaquita"):
        return AlpineOSUtil()

    if distro_name == "chainguard":
        return ChainguardOSUtil()

    if distro_name == "kali":
        return DebianOSBaseUtil()

    if distro_name in ("flatcar", "coreos") or distro_code_name in ("flatcar", "coreos"):
        return CoreOSUtil()

    if distro_name in ("suse", "sle-micro", "sle_hpc", "sles", "opensuse"):
        if distro_full_name == 'SUSE Linux Enterprise Server' \
                and DistroVersion(distro_version) < DistroVersion('12') \
                or distro_full_name == 'openSUSE' and DistroVersion(distro_version) < DistroVersion('13.2'):
            return SUSE11OSUtil()

        return SUSEOSUtil()

    if distro_name == "debian":
        if "sid" in distro_version or DistroVersion(distro_version) > DistroVersion("7"):
            return DebianOSModernUtil()

        return DebianOSBaseUtil()

    # Devuan support only works with v4+ 
    # Reason is that Devuan v4 (Chimaera) uses python v3.9, in which the 
    # platform.linux_distribution module has been removed. This was unable
    # to distinguish between debian and devuan. The new distro.linux_distribution module
    # is able to distinguish between the two.

    if distro_name == "devuan" and DistroVersion(distro_version) >= DistroVersion("4"):
        return DevuanOSUtil()
        
    if distro_name in ("redhat", "rhel", "centos", "oracle", "almalinux",
                       "cloudlinux", "rocky"):
        if DistroVersion(distro_version) < DistroVersion("7"):
            return Redhat6xOSUtil()

        if DistroVersion(distro_version) >= DistroVersion("8.6"):
            return RedhatOSModernUtil()

        return RedhatOSUtil()

    if distro_name == "euleros":
        return RedhatOSUtil()

    if distro_name == "uos":
        return RedhatOSUtil()

    if distro_name == "freebsd":
        return FreeBSDOSUtil()

    if distro_name == "openbsd":
        return OpenBSDOSUtil()

    if distro_name == "bigip":
        return BigIpOSUtil()

    if distro_name == "gaia":
        return GaiaOSUtil()

    if distro_name == "iosxe":
        return IosxeOSUtil()

    if distro_name in ["mariner", "azurelinux"]:
        return MarinerOSUtil()

    if distro_name == "nsbsd":
        return NSBSDOSUtil()

    if distro_name == "openwrt":
        return OpenWRTOSUtil()

    if distro_name == "fedora":
        return FedoraOSUtil()

    logger.warn("Unable to load distro implementation for {0}. Using default distro implementation instead.", distro_name)
    return DefaultOSUtil()
