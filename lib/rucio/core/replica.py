# Copyright European Organization for Nuclear Research (CERN)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# You may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
#
# Authors:
# - Vincent Garonne, <vincent.garonne@cern.ch>, 2013

from datetime import datetime, timedelta
from re import match

from sqlalchemy import func, and_, or_, exists
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.sql.expression import case

from rucio.common import exception
from rucio.common.utils import grouper
from rucio.core.rse import get_rse, get_rse_id
from rucio.core.rse_counter import decrease, increase
from rucio.db import models
from rucio.db.constants import DIDType, ReplicaState
from rucio.db.session import read_session, stream_session, transactional_session
from rucio.rse import rsemanager
from rucio.rse.rsemanager import RSEMgr


@stream_session
def list_replicas(dids, schemes=None, unavailable=False, session=None):
    """
    List file replicas for a list of data identifiers (DIDs).

    :param dids: The list of data identifiers (DIDs).
    :param schemes: A list of schemes to filter the replicas. (e.g. file, http, ...)
    :param unavailable: Also include unavailable replicas in the list.
    :param session: The database session in use.
    """
    replica_conditions = list()
    # Get files
    for did in dids:
        try:
            (did_type, ) = session.query(models.DataIdentifier.did_type).filter_by(scope=did['scope'], name=did['name']).one()
        except NoResultFound:
            raise exception.DataIdentifierNotFound("Data identifier '%(scope)s:%(name)s' not found" % did)

        if did_type == DIDType.FILE:
            if not unavailable:
                replica_conditions.append(and_(models.RSEFileAssociation.scope == did['scope'],
                                               models.RSEFileAssociation.name == did['name'],
                                               models.RSEFileAssociation.state == ReplicaState.AVAILABLE))
            else:
                replica_conditions.append(and_(models.RSEFileAssociation.scope == did['scope'],
                                               models.RSEFileAssociation.name == did['name'],
                                               or_(models.RSEFileAssociation.state == ReplicaState.AVAILABLE,
                                                   models.RSEFileAssociation.state == ReplicaState.UNAVAILABLE)))

        else:
            content_query = session.query(models.DataIdentifierAssociation)
            child_dids = [(did['scope'], did['name'])]
            while child_dids:
                s, n = child_dids.pop()
                for tmp_did in content_query.filter_by(scope=s, name=n):
                    if tmp_did.child_type == DIDType.FILE:
                        if not unavailable:
                            replica_conditions.append(and_(models.RSEFileAssociation.scope == tmp_did.child_scope,
                                                           models.RSEFileAssociation.name == tmp_did.child_name,
                                                           models.RSEFileAssociation.state == ReplicaState.AVAILABLE))
                        else:
                            replica_conditions.append(and_(models.RSEFileAssociation.scope == tmp_did.child_scope,
                                                           models.RSEFileAssociation.name == tmp_did.child_name,
                                                           or_(models.RSEFileAssociation.state == ReplicaState.AVAILABLE,
                                                               models.RSEFileAssociation.state == ReplicaState.UNAVAILABLE)))
                    else:
                        child_dids.append((tmp_did.child_scope, tmp_did.child_name))

    # Get replicas
    rsemgr = rsemanager.RSEMgr(server_mode=True)
    is_none = None
    replicas_conditions = grouper(replica_conditions, 10, and_(models.RSEFileAssociation.scope == is_none,
                                                               models.RSEFileAssociation.name == is_none,
                                                               models.RSEFileAssociation.state == is_none))
    replica_query = session.query(models.RSEFileAssociation, models.RSE.rse).join(models.RSE, models.RSEFileAssociation.rse_id == models.RSE.id).\
        order_by(models.RSEFileAssociation.scope).\
        order_by(models.RSEFileAssociation.name)
    dict_tmp_files = {}
    replicas = []
    for replica_condition in replicas_conditions:
        for replica, rse in replica_query.filter(or_(*replica_condition)).yield_per(5):
            key = '%s:%s' % (replica.scope, replica.name)
            if not key in dict_tmp_files:
                dict_tmp_files[key] = {'scope': replica.scope, 'name': replica.name, 'bytes': replica.bytes,
                                       'md5': replica.md5, 'adler32': replica.adler32,
                                       'rses': {rse: list()}}
            else:
                dict_tmp_files[key]['rses'][rse] = []
            result = rsemgr.list_protocols(rse_id=rse, session=session)
            for protocol in result:
                if not schemes or protocol['scheme'] in schemes:
                    dict_tmp_files[key]['rses'][rse].append(rsemgr.lfn2pfn(rse_id=rse, lfns={'scope': replica.scope, 'name': replica.name}, properties=protocol, session=session))
                    if protocol['scheme'] == 'srm':
                        try:
                            dict_tmp_files[key]['space_token'] = protocol['extended_attributes']['space_token']
                        except KeyError:
                            dict_tmp_files[key]['space_token'] = None
    for key in dict_tmp_files:
        replicas.append(dict_tmp_files[key])
        yield dict_tmp_files[key]


@transactional_session
def __bulk_add_new_file_dids(files, account, session=None):
    """
    Bulk add new dids.

    :param dids: the list of new files.
    :param account: The account owner.
    :param session: The database session in use.
    :returns: True is successful.
    """
    for file in files:
        new_did = models.DataIdentifier(scope=file['scope'], name=file['name'], account=file.get('account') or account, did_type=DIDType.FILE, bytes=file['bytes'], md5=file.get('md5'), adler32=file.get('adler32'))
        for key in file.get('meta', []):
            new_did.update({key: file['meta'][key]})
        new_did.save(session=session, flush=False)
    try:
        session.flush()
    except IntegrityError, e:
        raise exception.RucioException(e.args)
    except DatabaseError, e:
        raise exception.RucioException(e.args)
    return True


@transactional_session
def __bulk_add_file_dids(files, account, session=None):
    """
    Bulk add new dids.

    :param dids: the list of files.
    :param account: The account owner.
    :param session: The database session in use.
    :returns: True is successful.
    """
    condition = or_()
    for f in files:
        condition.append(and_(models.DataIdentifier.scope == f['scope'], models.DataIdentifier.name == f['name'], models.DataIdentifier.did_type == DIDType.FILE))

    q = session.query(models.DataIdentifier.scope,
                      models.DataIdentifier.name,
                      models.DataIdentifier.bytes,
                      models.DataIdentifier.adler32,
                      models.DataIdentifier.md5).filter(condition)
    available_files = [dict([(column, getattr(row, column)) for column in row._fields]) for row in q]
    new_files = list()
    for file in files:
        found = False
        for available_file in available_files:
            if file['scope'] == available_file['scope'] and file['name'] == available_file['name']:
                found = True
                break
        if not found:
            new_files.append(file)
    __bulk_add_new_file_dids(files=new_files, account=account, session=session)
    return new_files + available_files


@transactional_session
def __bulk_add_replicas(rse_id, files, account, session=None):
    """
    Bulk add new dids.

    :param rse_id: the RSE id.
    :param dids: the list of files.
    :param account: The account owner.
    :param session: The database session in use.
    :returns: True is successful.
    """
    nbfiles, bytes = 0, 0
    for file in files:
        nbfiles += 1
        bytes += file['bytes']
        new_replica = models.RSEFileAssociation(rse_id=rse_id, scope=file['scope'], name=file['name'], bytes=file['bytes'], path=file.get('path'), state=ReplicaState.AVAILABLE,
                                                md5=file.get('md5'), adler32=file.get('adler32'), tombstone=file.get('tombstone') or datetime.utcnow() + timedelta(weeks=2))
        new_replica.save(session=session, flush=False)
    try:
        session.flush()
        return nbfiles, bytes
    except IntegrityError, e:
        if match('.*IntegrityError.*ORA-00001: unique constraint .*REPLICAS_PK.*violated.*', e.args[0]) \
           or match('.*IntegrityError.*1062.*Duplicate entry.*', e.args[0]) \
           or e.args[0] == '(IntegrityError) columns rse_id, scope, name are not unique' \
           or match('.*IntegrityError.*duplicate key value violates unique constraint.*', e.args[0]):
                raise exception.Duplicate("File replica already exists!")
        raise exception.RucioException(e.args)
    except DatabaseError, e:
        raise exception.RucioException(e.args)


@transactional_session
def add_replicas(rse, files, account, session=None):
    """
    Bulk add file replicas.

    :param rse: the rse name.
    :param files: the list of files.
    :param account: The account owner.
    :param session: The database session in use.

    :returns: True is successful.
    """
    replica_rse = get_rse(rse=rse, session=session)

    replicas = __bulk_add_file_dids(files=files, account=account, session=session)

    if not replica_rse.deterministic:
        rse_manager = RSEMgr(server_mode=True)
        for file in files:
            if 'pfn' not in file:
                raise exception.UnsupportedOperation('PFN needed for this (non deterministic) RSE %(rse)s ' % locals())
            tmp = rse_manager.parse_pfn(rse_id=rse, pfn=file['pfn'], session=session)
            file['path'] = ''.join([tmp['prefix'], tmp['path'], tmp['name']]) if ('prefix' in tmp.keys()) and (tmp['prefix'] is not None) else ''.join([tmp['path'], tmp['name']])

    nbfiles, bytes = __bulk_add_replicas(rse_id=replica_rse.id, files=files, account=account, session=session)
    increase(rse_id=replica_rse.id, delta=nbfiles, bytes=bytes, session=session)
    return replicas


@transactional_session
def add_replica(rse, scope, name, bytes, account, adler32=None, md5=None, dsn=None, pfn=None, meta={}, rules=[], tombstone=None, session=None):
    """
    Add File replica.

    :param rse: the rse name.
    :param scope: the tag name.
    :param name: The data identifier name.
    :param bytes: the size of the file.
    :param account: The account owner.
    :param md5: The md5 checksum.
    :param adler32: The adler32 checksum.
    :param pfn: Physical file name (for nondeterministic rse).
    :param meta: Meta-data associated with the file. Represented as key/value pairs in a dictionary.
    :param rules: Replication rules associated with the file. A list of dictionaries, e.g., [{'copies': 2, 'rse_expression': 'TIERS1'}, ].
    :param tombstone: If True, create replica with a tombstone.
    :param session: The database session in use.

    :returns: True is successful.
    """
    return add_replicas(rse=rse, files=[{'scope': scope, 'name': name, 'bytes': bytes, 'pfn': pfn, 'adler32': adler32, 'md5': md5, 'meta': meta, 'rules': rules, 'tombstone': tombstone}, ], account=account, session=session)


@transactional_session
def delete_replicas(rse, files, session=None):
    """
    Delete file replicas.

    :param rse: the rse name.
    :param files: the list of files to delete.
    :param session: The database session in use.
    """
    replica_rse = get_rse(rse=rse, session=session)

    condition = or_()
    for file in files:
        condition.append(and_(models.RSEFileAssociation.scope == file['scope'],
                              models.RSEFileAssociation.name == file['name'],
                              models.RSEFileAssociation.rse_id == replica_rse.id))

    delta, bytes = 0, 0
    parent_condition = or_()
    replicas = list()
    for replica in session.query(models.RSEFileAssociation).filter(condition):

        replica.delete(session=session)

        parent_condition.append(and_(models.DataIdentifierAssociation.child_scope == replica.scope,
                                     models.DataIdentifierAssociation.child_name == replica.name,
                                     ~exists([1]).where(and_(models.RSEFileAssociation.scope == replica.scope, models.RSEFileAssociation.name == replica.name))))

        replicas.append((replica.scope, replica.name))
        bytes += replica['bytes']
        delta += 1

    if len(replicas) != len(files):
        raise exception.ReplicaNotFound(str(replicas))

    session.flush()

    # Delete did from the content for the last did
    query = session.query(models.DataIdentifierAssociation.scope, models.DataIdentifierAssociation.name,
                          models.DataIdentifierAssociation.child_scope, models.DataIdentifierAssociation.child_name).filter(parent_condition)

    parent_datasets = list()
    for parent_scope, parent_name, child_scope, child_name in query:
        rowcount = session.query(models.DataIdentifierAssociation).filter_by(scope=parent_scope, name=parent_name, child_scope=child_scope, child_name=child_name).\
            delete(synchronize_session=False)

        (parent_scope, parent_name) not in parent_datasets and parent_datasets.append((parent_scope, parent_name))

    # Delete empty closed collections
    # parent_condition = or_()
    deleted_parents = list()
    for parent_scope, parent_name in parent_datasets:
        rowcount = session.query(models.DataIdentifier).filter_by(scope=parent_scope, name=parent_name, is_open=False).\
            filter(~exists([1]).where(and_(models.DataIdentifierAssociation.scope == parent_scope, models.DataIdentifierAssociation.name == parent_name))).\
            delete(synchronize_session=False)

        if rowcount:
            deleted_parents.append((parent_scope, parent_name))
    #        parent_condition.append(and_(models.DataIdentifierAssociation.child_scope == parent_scope,
    #                                     models.DataIdentifierAssociation.child_name == parent_name,
    #                                     ~exists([1]).where(and_(models.DataIdentifierAssociation.scope == parent_scope,
    #                                                             models.DataIdentifierAssociation.name == parent_name))))
    #   if not deleted_parents:
    #        break

    # ToDo: delete empty datasets from container and delete empty close containers

    # Delete file with no replicas
    for replica_scope, replica_name in replicas:
        session.query(models.DataIdentifier).filter_by(scope=replica_scope, name=replica_name).\
            filter(~exists([1]).where(and_(models.RSEFileAssociation.scope == replica_scope, models.RSEFileAssociation.name == replica_name))).\
            delete(synchronize_session=False)

    # Decrease RSE counter
    decrease(rse_id=replica_rse.id, delta=delta, bytes=bytes, session=session)
    # Error handling foreign key constraint violation on commit


@transactional_session
def get_replica(rse, scope, name, rse_id=None, session=None):
    """
    Get File replica.

    :param rse: the rse name.
    :param scope: the scope name.
    :param name: The data identifier name.
    :param rse_id: The RSE Id.
    :param session: The database session in use.

    :returns: A dictionary with the list of replica attributes.
    """
    if not rse_id:
        rse_id = get_rse_id(rse=rse, session=session)

    row = session.query(models.RSEFileAssociation).filter_by(rse_id=rse_id, scope=scope, name=name).one()
    d = {}
    for column in row.__table__.columns:
        d[column.name] = getattr(row, column.name)
    return d


@read_session
def list_unlocked_replicas(rse, limit, bytes=None, rse_id=None, worker_number=None, total_workers=None, session=None):
    """
    List RSE File replicas with no locks.

    :param rse: the rse name.
    :param bytes: the amount of needed bytes.
    :param session: The database session in use.

    :returns: a list of dictionary replica.
    """

    if not rse_id:
        rse_id = get_rse_id(rse=rse, session=session)

    none_value = None  # Hack to get pep8 happy...
    query = session.query(models.RSEFileAssociation.scope, models.RSEFileAssociation.name, models.RSEFileAssociation.bytes).\
        filter(models.RSEFileAssociation.tombstone < datetime.utcnow()).\
        filter(models.RSEFileAssociation.lock_cnt == 0).\
        filter(case([(models.RSEFileAssociation.tombstone != none_value, models.RSEFileAssociation.rse_id), ]) == rse_id).\
        order_by(models.RSEFileAssociation.tombstone).\
        with_hint(models.RSEFileAssociation, "INDEX(replicas REPLICAS_TOMBSTONE_IDX)", 'oracle')

    if worker_number and total_workers and total_workers-1 > 0:
        if session.bind.dialect.name == 'oracle':
            query = query.filter('ORA_HASH(name, %s) = %s' % (total_workers-1, worker_number-1))
        elif session.bind.dialect.name == 'mysql':
            query = query.filter('mod(md5(name), %s) = %s' % (total_workers-1, worker_number-1))
        elif session.bind.dialect.name == 'postgresql':
            query = query.filter('mod(abs((\'x\'||md5(name))::bit(32)::int), %s) = %s' % (total_workers-1, worker_number-1))

    query = query.limit(limit)

    rows = list()
    #  neededSpace = bytes
    for (scope, name, bytes) in query.yield_per(5):
        d = {'scope': scope, 'name': name, 'bytes': bytes}
        rows.append(d)

    return rows


@read_session
def get_sum_count_being_deleted(rse_id, session=None):
    """

    :param rse_id: The id of the RSE.
    :param session: The database session in use.

    :returns: A dictionary with total and bytes.
    """
    none_value = None
    total, bytes = session.query(func.count(models.RSEFileAssociation.tombstone), func.sum(models.RSEFileAssociation.bytes)).filter_by(rse_id=rse_id).\
        filter(models.RSEFileAssociation.tombstone != none_value).\
        filter(models.RSEFileAssociation.state == ReplicaState.BEING_DELETED).\
        one()
    return {'bytes': bytes or 0, 'total': total or 0}


@transactional_session
def update_replicas_states(replicas, session=None):
    """
    Update File replica information and state.

    :param rse: the rse name.
    :param scope: the tag name.
    :param name: The data identifier name.
    :param state: The state.
    :param session: The database session in use.
    """

    rse_ids = {}
    for replica in replicas:
        if 'rse_id' not in replica:
            if replica['rse'] not in rse_ids:
                rse_ids[replica['rse']] = get_rse(rse=replica['rse'], session=session).id
            replica['rse_id'] = rse_ids[replica['rse']]

        query = session.query(models.RSEFileAssociation).filter_by(rse_id=replica['rse_id'], scope=replica['scope'], name=replica['name'])

        if replica['state'] == ReplicaState.BEING_DELETED:
            query = query.filter(lock_cnt=0)

        rowcount = query.update({'state': replica['state']})

        if not rowcount:
            raise exception.UnsupportedOperation('State for replica %(scope)s:%(name)s cannot be updated')


@transactional_session
def update_replica_state(rse, scope, name, state, session=None):
    """
    Update File replica information and state.

    :param rse: the rse name.
    :param scope: the tag name.
    :param name: The data identifier name.
    :param state: The state.
    :param session: The database session in use.
    """
    return update_replicas_states(replicas=[{'scope': scope, 'name': name, 'state': state, 'rse': rse}], session=session)


@transactional_session
def update_replica_lock_counter(rse, scope, name, value, rse_id=None, session=None):
    """
    Update File replica lock counters.

    :param rse: the rse name.
    :param scope: the tag name.
    :param name: The data identifier name.
    :param value: The number of created/deleted locks.
    :param rse_id: The id of the RSE.
    :param session: The database session in use.

    :returns: True or False.
    """
    if not rse_id:
        rse_id = get_rse_id(rse=rse, session=session)

    # WTF BUG in the mysql-driver: lock_cnt uses the already updated value! ACID? Never heard of it!

    if session.bind.dialect.name == 'mysql':
        rowcount = session.query(models.RSEFileAssociation).\
            filter_by(rse_id=rse_id, scope=scope, name=name).\
            update({'lock_cnt': models.RSEFileAssociation.lock_cnt + value,
                    'tombstone': case([(models.RSEFileAssociation.lock_cnt + value < 0,
                                        datetime.utcnow()), ],
                                      else_=None)},
                   synchronize_session=False)
    else:
        rowcount = session.query(models.RSEFileAssociation).\
            filter_by(rse_id=rse_id, scope=scope, name=name).\
            update({'lock_cnt': models.RSEFileAssociation.lock_cnt + value,
                    'tombstone': case([(models.RSEFileAssociation.lock_cnt + value == 0,
                                        datetime.utcnow()), ],
                                      else_=None)},
                   synchronize_session=False)

    return bool(rowcount)