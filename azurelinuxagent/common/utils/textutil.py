# Microsoft Azure Linux Agent
#
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

import base64
import re
import struct
import sys
import traceback
import xml.dom.minidom as minidom
import zlib

from azurelinuxagent.common.future import ustr


def parse_doc(xml_text):
    """
    Parse xml document from string
    """
    # The minidom lib has some issue with unicode in python2.
    # Encode the string into utf-8 first
    xml_text = xml_text.encode('utf-8')
    return minidom.parseString(xml_text)


def findall(root, tag, namespace=None):
    """
    Get all nodes by tag and namespace under Node root.
    """
    if root is None:
        return []

    if namespace is None:
        return root.getElementsByTagName(tag)
    else:
        return root.getElementsByTagNameNS(namespace, tag)


def find(root, tag, namespace=None):
    """
    Get first node by tag and namespace under Node root.
    """
    nodes = findall(root, tag, namespace=namespace)
    if nodes is not None and len(nodes) >= 1:
        return nodes[0]
    else:
        return None


def gettext(node):
    """
    Get node text
    """
    if node is None:
        return None

    for child in node.childNodes:
        if child.nodeType == child.TEXT_NODE:
            return child.data
    return None


def gettextxml(node):
    """
    Get the raw XML of a text node
    """
    if node is None:
        return None

    for child in node.childNodes:
        if child.nodeType == child.TEXT_NODE:
            return child.toxml()
    return None


def findtext(root, tag, namespace=None):
    """
    Get text of node by tag and namespace under Node root.
    """
    node = find(root, tag, namespace=namespace)
    return gettext(node)


def getattrib(node, attr_name):
    """
    Get attribute of xml node. Returns None if node is None. Returns "" if node does not have attribute attr_name
    """
    if node is not None:
        return node.getAttribute(attr_name)
    else:
        return None


def hasattrib(node, attr_name):
    """
    Return True if xml node has attribute, False if node is None or node does not have attribute attr_name
    """
    if node is not None:
        return node.hasAttribute(attr_name)
    else:
        return False


def unpack(buf, offset, value_range):
    """
    Unpack bytes into python values.
    """
    result = 0
    for i in value_range:
        result = (result << 8) | str_to_ord(buf[offset + i])
    return result


def unpack_little_endian(buf, offset, length):
    """
    Unpack little endian bytes into python values.
    """
    return unpack(buf, offset, list(range(length - 1, -1, -1)))


def unpack_big_endian(buf, offset, length):
    """
    Unpack big endian bytes into python values.
    """
    return unpack(buf, offset, list(range(0, length)))


def hex_dump3(buf, offset, length):
    """
    Dump range of buf in formatted hex.
    """
    return ''.join(['%02X' % str_to_ord(char) for char in buf[offset:offset + length]])


def hex_dump2(buf):
    """
    Dump buf in formatted hex.
    """
    return hex_dump3(buf, 0, len(buf))


def is_in_range(a, low, high):
    """
    Return True if 'a' in 'low' <= a <= 'high'
    """
    return low <= a <= high


def is_printable(ch):
    """
    Return True if character is displayable.
    """
    return (is_in_range(ch, str_to_ord('A'), str_to_ord('Z'))
            or is_in_range(ch, str_to_ord('a'), str_to_ord('z'))
            or is_in_range(ch, str_to_ord('0'), str_to_ord('9')))


def hex_dump(buffer, size):  # pylint: disable=redefined-builtin
    """
    Return Hex formated dump of a 'buffer' of 'size'.
    """
    if size < 0:
        size = len(buffer)
    result = ""
    for i in range(0, size):
        if (i % 16) == 0:
            result += "%06X: " % i
        byte = buffer[i]
        if type(byte) == str:
            byte = ord(byte.decode('latin1'))
        result += "%02X " % byte
        if (i & 15) == 7:
            result += " "
        if ((i + 1) % 16) == 0 or (i + 1) == size:
            j = i
            while ((j + 1) % 16) != 0:
                result += "   "
                if (j & 7) == 7:
                    result += " "
                j += 1
            result += " "
            for j in range(i - (i % 16), i + 1):
                byte = buffer[j]
                if type(byte) == str:
                    byte = str_to_ord(byte.decode('latin1'))
                k = '.'
                if is_printable(byte):
                    k = chr(byte)
                result += k
            if (i + 1) != size:
                result += "\n"
    return result


def str_to_ord(a):
    """
    Allows indexing into a string or an array of integers transparently.
    Generic utility function.
    """
    if type(a) == type(b'') or type(a) == type(u''):
        a = ord(a)
    return a


def compare_bytes(a, b, start, length):
    for offset in range(start, start + length):
        if str_to_ord(a[offset]) != str_to_ord(b[offset]):
            return False
    return True


def int_to_ip4_addr(a):
    """
    Build DHCP request string.
    """
    return "%u.%u.%u.%u" % ((a >> 24) & 0xFF,
                            (a >> 16) & 0xFF,
                            (a >> 8) & 0xFF,
                            (a) & 0xFF)


def hexstr_to_bytearray(a):
    """
    Return hex string packed into a binary struct.
    """
    b = b""
    for c in range(0, len(a) // 2):
        b += struct.pack("B", int(a[c * 2:c * 2 + 2], 16))
    return b


def set_ssh_config(config, name, val):
    found = False
    no_match = -1

    match_start = no_match
    for i in range(0, len(config)):
        if config[i].startswith(name) and match_start == no_match:
            config[i] = "{0} {1}".format(name, val)
            found = True
        elif config[i].lower().startswith("match"):
            if config[i].lower().startswith("match all"):
                # outside match block
                match_start = no_match
            elif match_start == no_match:
                # inside match block
                match_start = i
    if not found:
        if match_start != no_match:
            i = match_start
        config.insert(i, "{0} {1}".format(name, val))
    return config


def set_ini_config(config, name, val):
    notfound = True
    nameEqual = name + '='
    length = len(config)
    text = "{0}=\"{1}\"".format(name, val)

    for i in reversed(range(0, length)):
        if config[i].startswith(nameEqual):
            config[i] = text
            notfound = False
            break

    if notfound:
        config.insert(length - 1, text)


def replace_non_ascii(incoming, replace_char=''):
    outgoing = ''
    if incoming is not None:
        for c in incoming:
            if str_to_ord(c) > 128:
                outgoing += replace_char
            else:
                outgoing += c
    return outgoing


def remove_bom(c):
    """
    bom is comprised of a sequence of three chars,0xef, 0xbb, 0xbf, in case of utf-8.
    """
    if not is_str_none_or_whitespace(c) and \
       len(c) > 2 and \
       str_to_ord(c[0]) > 128 and \
       str_to_ord(c[1]) > 128 and \
       str_to_ord(c[2]) > 128:
        c = c[3:]
    return c


def get_bytes_from_pem(pem_str):
    base64_bytes = ""
    for line in pem_str.split('\n'):
        if "----" not in line:
            base64_bytes += line
    return base64_bytes


def compress(s):
    """
    Compress a string, and return the base64 encoded result of the compression.

    This method returns a string instead of a byte array.  It is expected
    that this method is called to compress smallish strings, not to compress
    the contents of a file. The output of this method is suitable for
    embedding in log statements.
    """
    if sys.version_info[0] > 2:
        return base64.b64encode(zlib.compress(bytes(s, 'utf-8'))).decode('utf-8')
    return base64.b64encode(zlib.compress(s))


def b64encode(s):
    if sys.version_info[0] > 2:
        return base64.b64encode(bytes(s, 'utf-8')).decode('utf-8')
    return base64.b64encode(s)


def b64decode(s):
    if sys.version_info[0] > 2:
        return base64.b64decode(s).decode('utf-8')
    return base64.b64decode(s)


def safe_shlex_split(s):
    import shlex
    if sys.version_info[:2] == (2, 6):
        return shlex.split(s.encode('utf-8'))
    return shlex.split(s)


def swap_hexstring(s, width=2):
    r = len(s) % width
    if r != 0:
        s = ('0' * (width - (len(s) % width))) + s

    return ''.join(reversed(
                        re.findall( 
                                r'[a-f0-9]{{{0}}}'.format(width), 
                                s, 
                                re.IGNORECASE))) 


def parse_json(json_str):
    """
    Parse json string and return a resulting dictionary
    """
    # trim null and whitespaces
    result = None
    if not is_str_empty(json_str):
        import json
        result = json.loads(json_str.rstrip(' \t\r\n\0'))

    return result


def is_str_none_or_whitespace(s):
    return s is None or len(s) == 0 or s.isspace()


def is_str_empty(s):
    return is_str_none_or_whitespace(s) or is_str_none_or_whitespace(s.rstrip(' \t\r\n\0'))


def format_memory_value(unit, value):
    units = {'bytes': 1, 'kilobytes': 1024, 'megabytes': 1024*1024, 'gigabytes': 1024*1024*1024}

    if unit not in units:
        raise ValueError("Unit must be one of {0}".format(units.keys()))
    try:
        value = float(value)
    except TypeError:
        raise TypeError('Value must be convertible to a float')

    return int(value * units[unit])


def str_to_encoded_ustr(s, encoding='utf-8'):
    """
    This function takes the string and converts it into the corresponding encoded ustr if its not already a ustr.
    The encoding is utf-8 by default if not specified.
    Note: ustr() is a unicode object for Py2 and a str object for Py3.
    :param s: The string to convert to ustr
    :param encoding: Encoding to use. Utf-8 by default
    :return: Returns the corresponding ustr string. Returns None if input is None.
    """

    if s is None or type(s) is ustr:
        # If its already a ustr/None then return as is
        return s
    if sys.version_info[0] > 2:
        try:
            # For py3+, str() is unicode by default
            if isinstance(s, bytes):
                # str.encode() returns bytes which should be decoded to get the str.
                return s.decode(encoding)
            else:
                # If its not encoded, just return the string
                return ustr(s)
        except Exception:
            # If some issues in decoding, just return the string
            return ustr(s)
    # For Py2, explicitly convert the string to unicode with the specified encoding
    return ustr(s, encoding=encoding)


def format_exception(exception):
    # Function to format exception message
    e = None
    if sys.version_info[0] == 2:
        _, e, tb = sys.exc_info()
    else:
        tb = exception.__traceback__

    msg = ustr(exception) + "\n"
    if tb is None or (sys.version_info[0] == 2 and e != exception):
        msg += "[Traceback not available]"
    else:
        msg += ''.join(traceback.format_exception(type(exception), value=exception, tb=tb))

    return msg


SAS_TOKEN_RE = re.compile(r'(https://\S+\?)((sv|st|se|sr|sp|sip|spr|sig)=\S+)+', flags=re.IGNORECASE)


def redact_sas_token(msg):
    """
    Redact SAS tokens from the message
    """
    if msg is None:
        return msg
    return SAS_TOKEN_RE.sub(r'\1<redacted>', msg)


