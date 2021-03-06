#
# Copyright (C) 2016 Red Hat, Inc.
#
#  Licensed under the Apache License, Version 2.0 (the "License"); you may
#  not use this file except in compliance with the License. You may obtain
#  a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#  WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#  License for the specific language governing permissions and limitations
#  under the License.
#

from neutron_lib import exceptions as nexc
from neutron_lib.plugins import directory
from oslo_log import log as logging

from networking_odl._i18n import _
from networking_odl.common import client
from networking_odl.common import constants as odl_const
from networking_odl.db import db
from networking_odl.journal import full_sync
from networking_odl.journal import journal

_CLIENT = client.OpenDaylightRestClientGlobal()

LOG = logging.getLogger(__name__)


class UnsupportedResourceType(Exception):
    pass


def journal_recovery(context):
    for row in db.get_all_db_rows_by_state(context.session, odl_const.FAILED):
        try:
            LOG.debug("Attempting recovery of journal entry %s.", row)
            odl_resource = _CLIENT.get_client().get_resource(row.object_type,
                                                             row.object_uuid)
            if odl_resource is not None:
                _handle_existing_resource(context, row)
            else:
                _handle_non_existing_resource(context, row)
        except UnsupportedResourceType:
            LOG.warning('Unsupported resource %s', row.object_type)
        except Exception:
            LOG.exception("Failure while recovering journal entry %s.", row)


def _get_latest_resource(context, row):
    object_type = row.object_type

    for plugin_alias, resources in full_sync.ALL_RESOURCES.items():
        if object_type in resources:
            plugin = directory.get_plugin(plugin_alias)
            break
    else:
        raise UnsupportedResourceType(
            _("unsupported resource type: {}").format(object_type))

    obj_getter = getattr(plugin, 'get_{}'.format(object_type))
    return obj_getter(context, row.object_uuid)


def _sync_resource_to_odl(context, row, operation_type, exists_on_odl):
    resource = None
    try:
        resource = _get_latest_resource(context, row)
    except nexc.NotFound:
        if exists_on_odl:
            journal.record(context, row.object_type,
                           row.object_uuid, odl_const.ODL_DELETE, [])
    else:
        journal.record(context, row.object_type, row.object_uuid,
                       operation_type, resource)

    db.update_db_row_state(context.session, row, odl_const.COMPLETED)


def _handle_existing_resource(context, row):
    if row.operation == odl_const.ODL_CREATE:
        db.update_db_row_state(context.session, row, odl_const.COMPLETED)
    elif row.operation == odl_const.ODL_DELETE:
        db.update_db_row_state(context.session, row, odl_const.PENDING)
    else:
        _sync_resource_to_odl(context, row, odl_const.ODL_UPDATE, True)


def _handle_non_existing_resource(context, row):
    if row.operation == odl_const.ODL_DELETE:
        db.update_db_row_state(context.session, row, odl_const.COMPLETED)
    else:
        _sync_resource_to_odl(context, row, odl_const.ODL_CREATE, False)
        # TODO(mkolesni): Handle missing parent resources somehow.
