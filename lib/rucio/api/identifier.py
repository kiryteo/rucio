# Copyright European Organization for Nuclear Research (CERN)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# You may not use this file except in compliance with the License.
# You may obtain a copy of the
# License at http://www.apache.org/licenses/LICENSE-2.0
#
# Authors:
# - Vincent Garonne, <vincent.garonne@cern.ch>, 2012
# - Mario Lassnig, <mario.lassnig@cern.ch>, 2012

import rucio.api.permission

from rucio.core import identifier


def list_replicas(scope, did):
    """
    List file replicas for a data identifier.

    :param scope: The scope name.
    :param did: The data identifier.
    """

    return identifier.list_replicas(scope=scope, did=did)


def add_identifier(scope, did, sources, issuer):
    """
    Add data identifier.

    :param scope: The scope name.
    :param did: The data identifier.
    :param sources: The content as a list of data identifiers.
    :param issuer: The issuer account.
    """
    kwargs = {'scope': scope, 'did': did, 'sources': sources, 'issuer': issuer}
    if not rucio.api.permission.has_permission(issuer=issuer, action='add_identifier', kwargs=kwargs):
        raise rucio.common.exception.AccessDenied('Account %s can not add data identifier to scope %s' % (issuer, scope))
    return identifier.add_identifier(scope=scope, did=did, sources=sources, issuer=issuer)


def list_content(scope, did):
    """
    List data identifier contents.

    :param scope: The scope name.
    :param did: The data identifier.
    """

    return identifier.list_content(scope=scope, did=did)


def list_files(scope, did):
    """
    List data identifier file contents.

    :param scope: The scope name.
    :param did: The data identifier.
    """

    return identifier.list_files(scope=scope, did=did)


def scope_list(scope):
    """
    List data identifiers in a scope.

    :param scope: The scope name.
    """

    return identifier.scope_list(scope=scope)


def get_did(scope, did):
    """
    Retrieve a single data identifier.

    :param scope: The scope name.
    :param did: The data identifier.
    :return did: Dictionary containing {'did', 'scope', 'type'}, Exception otherwise
    """

    return identifier.get_did(scope=scope, did=did)
