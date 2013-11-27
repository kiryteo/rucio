# Copyright European Organization for Nuclear Research (CERN)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# You may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
#
# Authors:
# - Vincent Garonne, <vincent.garonne@cern.ch>, 2013

from json import dumps
from requests.status_codes import codes

from rucio.client.baseclient import BaseClient
from rucio.common.utils import build_url, render_json


class ReplicaClient(BaseClient):
    """Replica client class for working with replicas"""

    REPLICAS_BASEURL = 'replicas'

    def __init__(self, rucio_host=None, auth_host=None, account=None, ca_cert=None, auth_type=None, creds=None, timeout=None):
        super(ReplicaClient, self).__init__(rucio_host, auth_host, account, ca_cert, auth_type, creds, timeout)

    def list_replicas(self, dids, schemes=None, unavailable=False):
        """
        List file replicas for a list of data identifiers (DIDs).

        :param dids: The list of data identifiers (DIDs).
        :param schemes: A list of schemes to filter the replicas. (e.g. file, http, ...)
        :param unavailable: Also include unavailable replicas in the list.
        """
        data = {'dids': dids}
        if schemes:
            data['schemes'] = schemes
        if unavailable:
            data['unavailable'] = True
        url = build_url(self.host, path=self.REPLICAS_BASEURL, params=dumps(data))
        # pass json dict in querystring
        r = self._send_request(url, type='GET')
        if r.status_code == codes.ok:
            replicas = self._load_json_data(r)
            return replicas
        exc_cls, exc_msg = self._get_exception(r.headers)
        raise exc_cls(exc_msg)

    def add_replica(self, rse, scope, name, bytes, adler32, md5=None, meta={}):
        """
        Add file replicas to a RSE.

        :param rse: the RSE name.
        :param files: The list of files.

        :return: True if files were created successfully.
        """
        return self.add_replicas(rse=rse, files=[{'scope': scope, 'name': name, 'bytes': bytes, 'meta': meta, 'adler32': adler32, 'md5': md5}])

    def add_replicas(self, rse, files):
        """
        Bulk add file replicas to a RSE.

        :param rse: the RSE name.
        :param files: The list of files.

        :return: True if files were created successfully.
        """
        url = build_url(self.host, path=self.REPLICAS_BASEURL)
        data = {'rse': rse, 'files': files}
        r = self._send_request(url, type='POST', data=render_json(**data))
        if r.status_code == codes.created:
            return True
        exc_cls, exc_msg = self._get_exception(r.headers)
        raise exc_cls(exc_msg)

    def delete_replicas(self, rse, files):
        """
        Bulk delete file replicas from a RSE.

        :param rse: the RSE name.
        :param files: The list of files.

        :return: True if files have been deleted successfully.
        """
        url = build_url(self.host, path=self.REPLICAS_BASEURL)
        data = {'rse': rse, 'files': files}
        r = self._send_request(url, type='DEL', data=render_json(**data))
        if r.status_code == codes.ok:
            return True
        exc_cls, exc_msg = self._get_exception(r.headers)
        raise exc_cls(exc_msg)