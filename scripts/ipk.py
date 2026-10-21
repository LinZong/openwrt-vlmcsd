#!/usr/bin/env python3
"""Read native OpenWrt IPKs (tar or ar container), without executing package scripts."""
import io
import tarfile
from pathlib import Path


def members(path):
    data = Path(path).read_bytes()
    if data.startswith(b'!<arch>\n'):
        result = {}
        offset = 8
        while offset < len(data):
            header = data[offset:offset + 60]
            if len(header) != 60 or header[58:] != b'`\n':
                raise ValueError('Invalid IPK ar header')
            name = header[:16].decode('ascii').strip().rstrip('/')
            size = int(header[48:58])
            offset += 60
            value = data[offset:offset + size]
            if len(value) != size:
                raise ValueError('Truncated IPK ar member')
            result[name] = value
            offset += size + size % 2
        return result
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
        return {m.name.removeprefix('./'): archive.extractfile(m).read()
                for m in archive if m.isfile()}


def archive(path, kind):
    entries = members(path)
    matches = [value for name, value in entries.items() if name.startswith(kind + '.tar')]
    if len(matches) != 1 or entries.get('debian-binary', b'').strip() != b'2.0':
        raise ValueError('Invalid native IPK structure')
    return tarfile.open(fileobj=io.BytesIO(matches[0]), mode='r:*')


def metadata(path):
    with archive(path, 'control') as control:
        member = next(m for m in control if m.name.removeprefix('./') == 'control')
        fields = {}
        last = None
        for line in control.extractfile(member).read().decode().splitlines():
            if line.startswith((' ', '\t')) and last:
                fields[last] += ' ' + line.strip()
            elif ':' in line:
                last, value = line.split(':', 1)
                fields[last] = value.strip()
    return {'name': fields['Package'], 'version': fields['Version'], 'arch': fields['Architecture'],
            'depends': [dep.strip() for dep in fields.get('Depends', '').split(',') if dep.strip()]}


def payload_file(path, filename):
    with archive(path, 'data') as data:
        for member in data:
            if member.name.removeprefix('./') == filename.lstrip('/') and member.isfile():
                return data.extractfile(member).read()
    raise ValueError('Missing IPK payload file: ' + filename)
