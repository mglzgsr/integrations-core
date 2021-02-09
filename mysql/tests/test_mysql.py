# (C) Datadog, Inc. 2018-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
import copy
import json
import subprocess
import time
from collections import Counter
from contextlib import closing
from os import environ

import mock
import psutil
import pymysql
import pytest
from datadog_checks.base.utils.db.statement_samples import statement_samples_client
from datadog_checks.base.utils.platform import Platform
from datadog_checks.dev.utils import get_metadata_metrics
from datadog_checks.mysql import MySql, statements
from datadog_checks.mysql.version_utils import get_version
from pkg_resources import parse_version

from . import common, tags, variables
from .common import MYSQL_VERSION_PARSED


@pytest.mark.integration
@pytest.mark.usefixtures('dd_environment')
def test_minimal_config(aggregator, instance_basic):
    mysql_check = MySql(common.CHECK_NAME, {}, [instance_basic])
    mysql_check.check(instance_basic)

    # Test service check
    aggregator.assert_service_check('mysql.can_connect', status=MySql.OK, tags=tags.SC_TAGS_MIN, count=1)

    # Test metrics
    testable_metrics = (
            variables.STATUS_VARS
            + variables.VARIABLES_VARS
            + variables.INNODB_VARS
            + variables.BINLOG_VARS
            + variables.SYSTEM_METRICS
            + variables.SYNTHETIC_VARS
    )

    for mname in testable_metrics:
        aggregator.assert_metric(mname, at_least=0)


@pytest.mark.integration
@pytest.mark.usefixtures('dd_environment')
def test_complex_config(aggregator, instance_complex):
    mysql_check = MySql(common.CHECK_NAME, {}, instances=[instance_complex])
    mysql_check.check(instance_complex)

    _assert_complex_config(aggregator)


@pytest.mark.e2e
def test_e2e(dd_agent_check, instance_complex):
    aggregator = dd_agent_check(instance_complex)

    _assert_complex_config(aggregator)
    aggregator.assert_metrics_using_metadata(get_metadata_metrics(), exclude=['alice.age', 'bob.age'])


def _assert_complex_config(aggregator):
    # Test service check
    aggregator.assert_service_check('mysql.can_connect', status=MySql.OK, tags=tags.SC_TAGS, count=1)
    aggregator.assert_service_check('mysql.replication.slave_running', status=MySql.OK, tags=tags.SC_TAGS, at_least=1)
    testable_metrics = (
            variables.STATUS_VARS
            + variables.VARIABLES_VARS
            + variables.INNODB_VARS
            + variables.BINLOG_VARS
            + variables.SYSTEM_METRICS
            + variables.SCHEMA_VARS
            + variables.SYNTHETIC_VARS
    )

    if MYSQL_VERSION_PARSED >= parse_version('5.6'):
        testable_metrics.extend(variables.PERFORMANCE_VARS)

    # Test metrics
    for mname in testable_metrics:
        # These two are currently not guaranteed outside of a Linux
        # environment.
        if mname == 'mysql.performance.user_time' and not Platform.is_linux():
            continue
        if mname == 'mysql.performance.kernel_time' and not Platform.is_linux():
            continue
        if mname == 'mysql.performance.cpu_time' and Platform.is_windows():
            continue

        if mname == 'mysql.performance.query_run_time.avg':
            aggregator.assert_metric(mname, tags=tags.METRIC_TAGS + ['schema:testdb'], count=1)
            aggregator.assert_metric(mname, tags=tags.METRIC_TAGS + ['schema:mysql'], count=1)
        elif mname == 'mysql.info.schema.size':
            aggregator.assert_metric(mname, tags=tags.METRIC_TAGS + ['schema:testdb'], count=1)
            aggregator.assert_metric(mname, tags=tags.METRIC_TAGS + ['schema:information_schema'], count=1)
            aggregator.assert_metric(mname, tags=tags.METRIC_TAGS + ['schema:performance_schema'], count=1)
        else:
            aggregator.assert_metric(mname, tags=tags.METRIC_TAGS, at_least=0)

    # TODO: test this if it is implemented
    # Assert service metadata
    # version_metadata = mysql_check.service_metadata['version']
    # assert len(version_metadata) == 1

    # test custom query metrics
    aggregator.assert_metric('alice.age', value=25)
    aggregator.assert_metric('bob.age', value=20)

    # test optional metrics
    optional_metrics = (
            variables.OPTIONAL_REPLICATION_METRICS
            + variables.OPTIONAL_INNODB_VARS
            + variables.OPTIONAL_STATUS_VARS
            + variables.OPTIONAL_STATUS_VARS_5_6_6
    )
    _test_optional_metrics(aggregator, optional_metrics, 1)

    # Raises when coverage < 100%
    aggregator.assert_all_metrics_covered()


@pytest.mark.integration
@pytest.mark.usefixtures('dd_environment')
def test_connection_failure(aggregator, instance_error):
    """
    Service check reports connection failure
    """
    mysql_check = MySql(common.CHECK_NAME, {}, instances=[instance_error])

    with pytest.raises(Exception):
        mysql_check.check(instance_error)

    aggregator.assert_service_check('mysql.can_connect', status=MySql.CRITICAL, tags=tags.SC_FAILURE_TAGS, count=1)

    aggregator.assert_all_metrics_covered()


@pytest.mark.integration
@pytest.mark.usefixtures('dd_environment')
def test_complex_config_replica(aggregator, instance_complex):
    config = copy.deepcopy(instance_complex)
    config['port'] = common.SLAVE_PORT
    mysql_check = MySql(common.CHECK_NAME, {}, instances=[config])

    mysql_check.check(config)

    # self.assertMetricTag('mysql.replication.seconds_behind_master', 'channel:default')

    # Test service check
    aggregator.assert_service_check('mysql.can_connect', status=MySql.OK, tags=tags.SC_TAGS_REPLICA, count=1)

    # Travis MySQL not running replication - FIX in flavored test.
    aggregator.assert_service_check(
        'mysql.replication.slave_running', status=MySql.OK, tags=tags.SC_TAGS_REPLICA, at_least=1
    )

    testable_metrics = (
            variables.STATUS_VARS
            + variables.VARIABLES_VARS
            + variables.INNODB_VARS
            + variables.BINLOG_VARS
            + variables.SYSTEM_METRICS
            + variables.SCHEMA_VARS
            + variables.SYNTHETIC_VARS
    )

    if MYSQL_VERSION_PARSED >= parse_version('5.6') and environ.get('MYSQL_FLAVOR') != 'mariadb':
        testable_metrics.extend(variables.PERFORMANCE_VARS)

    # Test metrics
    for mname in testable_metrics:
        # These two are currently not guaranteed outside of a Linux
        # environment.
        if mname == 'mysql.performance.user_time' and not Platform.is_linux():
            continue
        if mname == 'mysql.performance.kernel_time' and not Platform.is_linux():
            continue
        if mname == 'mysql.performance.cpu_time' and Platform.is_windows():
            continue
        if mname == 'mysql.performance.query_run_time.avg':
            aggregator.assert_metric(mname, tags=tags.METRIC_TAGS + ['schema:testdb'], at_least=1)
        elif mname == 'mysql.info.schema.size':
            aggregator.assert_metric(mname, tags=tags.METRIC_TAGS + ['schema:testdb'], count=1)
            aggregator.assert_metric(mname, tags=tags.METRIC_TAGS + ['schema:information_schema'], count=1)
            aggregator.assert_metric(mname, tags=tags.METRIC_TAGS + ['schema:performance_schema'], count=1)
        else:
            aggregator.assert_metric(mname, tags=tags.METRIC_TAGS, at_least=0)

    # test custom query metrics
    aggregator.assert_metric('alice.age', value=25)
    aggregator.assert_metric('bob.age', value=20)

    # test optional metrics
    optional_metrics = (
            variables.OPTIONAL_REPLICATION_METRICS
            + variables.OPTIONAL_INNODB_VARS
            + variables.OPTIONAL_STATUS_VARS
            + variables.OPTIONAL_STATUS_VARS_5_6_6
    )
    _test_optional_metrics(aggregator, optional_metrics, 1)

    # Raises when coverage < 100%
    aggregator.assert_all_metrics_covered()


@pytest.mark.integration
@pytest.mark.usefixtures('dd_environment')
def test_statement_metrics(aggregator, instance_complex):
    QUERY = 'select * from information_schema.processlist'
    QUERY_DIGEST_TEXT = 'SELECT * FROM `information_schema` . `processlist`'
    # The query signature should match the query and consistency of this tag has product impact. Do not change
    # the query signature for this test unless you know what you're doing. The query digest is determined by
    # mysql and varies across versions.
    QUERY_SIGNATURE = '8cd0f2b4343decc'
    if environ.get('MYSQL_FLAVOR') == 'mariadb':
        QUERY_DIGEST = '5d343195f2d7adf4388d42755311c3e3'
    elif environ.get('MYSQL_VERSION') == '5.6':
        QUERY_DIGEST = 'acfa199773950cd8cf912f3a19219492'
    elif environ.get('MYSQL_VERSION') == '5.7':
        QUERY_DIGEST = '0737e429dc883ba8c86c15ae76e59dda'
    else:
        # 8.0+
        QUERY_DIGEST = '6817a67871eb7edddad5b7836c93330aa3c98801ac759eed1bea6db1a34579c4'
        QUERY_SIGNATURE = '9d73cb71644af0a2'

    config = copy.deepcopy(instance_complex)
    mysql_check = MySql(common.CHECK_NAME, {}, instances=[config])

    def run_query(q):
        with mysql_check._connect() as db:
            with closing(db.cursor()) as cursor:
                cursor.execute(q)

    # Run a query
    run_query(QUERY)
    mysql_check.check(config)

    # Run the query and check a second time so statement metrics are computed from the previous run
    run_query(QUERY)
    mysql_check.check(config)
    for name in statements.STATEMENT_METRICS.values():
        aggregator.assert_metric(
            name,
            tags=tags.SC_TAGS
                 + [
                     'query:{}'.format(QUERY_DIGEST_TEXT),
                     'query_signature:{}'.format(QUERY_SIGNATURE),
                     'digest:{}'.format(QUERY_DIGEST),
                 ],
            count=1,
        )


@pytest.mark.integration
@pytest.mark.usefixtures('dd_environment')
@pytest.mark.parametrize("events_statements_table", [
    "events_statements_current",
    "events_statements_history",
    "events_statements_history_long",
    None
])
@pytest.mark.parametrize("explain_strategy", [
    'PROCEDURE',
    'FQ_PROCEDURE',
    'STATEMENT',
    None
])
@pytest.mark.parametrize("schema,statement", [
    (None, 'select name as nam from testdb.users'),
    ('datadog', 'select name as nam from testdb.users'),
    ('testdb', 'select name as nam from users'),
])
def test_statement_samples_collect(instance_complex, events_statements_table, explain_strategy, schema, statement):
    # try to collect a sample from all supported events_statements tables using all possible strategies
    config = copy.deepcopy(instance_complex)
    config['statement_samples'] = {
        'enabled': True,
        'run_sync': True,
        'events_statements_table': events_statements_table
    }
    mysql_check = MySql(common.CHECK_NAME, {}, instances=[config])
    if explain_strategy:
        mysql_check._statement_samples._preferred_explain_strategies = [explain_strategy]

    mysql_check.check(config)
    statement_samples_client._events = []
    mysql_check._statement_samples._init_caches()

    # we deliberately want to keep the connection open for the duration of the test to ensure
    # the query remains in the events_statements_current and events_statements_history tables
    # it would be cleared out upon connection close otherwise
    db = pymysql.connect(**mysql_check._get_connection_args())
    with closing(db.cursor()) as cursor:
        # run the check once, then clear out all saved events
        # on the next check run it should only capture events since the last checkpoint
        if schema:
            cursor.execute("use {}".format(schema))
        cursor.execute(statement)
    mysql_check.check(config)
    matching = [e for e in statement_samples_client._events if e['db']['statement'] == statement]
    assert len(matching) > 0, "should have collected an event"
    with_plans = [e for e in matching if e['db']['plan']['definition'] is not None]
    if schema == 'testdb' and explain_strategy == 'FQ_PROCEDURE':
        # explain via the FQ_PROCEDURE will fail if a query contains non-fully-qualified tables because it will
        # default to the schema of the FQ_PROCEDURE, so in case of "select * from testdb" it'll try to do
        # "select start from datadog.testdb" which would be the wrong schema.
        assert not with_plans, "cannot collect plan in this case"
    elif not schema and explain_strategy == 'PROCEDURE':
        # if there is no default schema then we cannot use the non-fully-qualified procedure strategy
        assert not with_plans, "cannot collect plan in this case"
    else:
        event = with_plans[0]
        assert 'query_block' in json.loads(event['db']['plan']['definition']), "invalid json execution plan"

    # we avoid closing these in a try/finally block in order to maintain the connections in case we want to
    # debug the test with --pdb
    mysql_check._statement_samples._db.close()
    db.close()


@pytest.mark.integration
@pytest.mark.usefixtures('dd_environment')
def test_statement_samples_loop_inactive_stop(aggregator, instance_complex):
    # confirm that the collection loop stops on its own after the check has not been run for a while
    config = copy.deepcopy(instance_complex)
    config['min_collection_interval'] = 1
    config['statement_samples'] = {'enabled': True, 'run_sync': False}
    mysql_check = MySql(common.CHECK_NAME, {}, instances=[config])
    mysql_check.check(config)
    while mysql_check._statement_samples._collection_loop_future.running():
        time.sleep(0.1)
    aggregator.assert_metric("dd.mysql.statement_samples.collection_loop_inactive_stop")


@pytest.mark.integration
@pytest.mark.usefixtures('dd_environment')
def test_statement_samples_max_per_digest(aggregator, instance_complex):
    # clear out any events from previous test runs
    statement_samples_client._events = []

    # try to collect a sample from all supported events_statements tables using all possible strategies
    config = copy.deepcopy(instance_complex)
    config['statement_samples'] = {
        'enabled': True,
        'run_sync': True,
        'events_statements_table': 'events_statements_history_long'
    }
    mysql_check = MySql(common.CHECK_NAME, {}, instances=[config])

    for _ in range(3):
        mysql_check.check(config)

    rows = mysql_check._statement_samples._get_new_events_statements('events_statements_history_long', 1000)

    count_by_digest = Counter(r['digest'] for r in rows)
    for digest, count in count_by_digest.items():
        assert count == 1, "we should be reading exactly one row per digest out of the database"


@pytest.mark.integration
@pytest.mark.usefixtures('dd_environment')
def test_statement_samples_invalid_explain_procedure(aggregator, instance_complex):
    config = copy.deepcopy(instance_complex)
    config['min_collection_interval'] = 1
    config['statement_samples'] = {'enabled': True, 'run_sync': True, 'explain_procedure': "hello"}
    mysql_check = MySql(common.CHECK_NAME, {}, instances=[config])
    mysql_check.check(config)
    aggregator.assert_metric_has_tag_prefix("dd.mysql.statement_samples.error", "error:explain-")


def _test_optional_metrics(aggregator, optional_metrics, at_least):
    """
    Check optional metrics - there should be at least `at_least` matches
    """

    before = len(aggregator.not_asserted())

    for mname in optional_metrics:
        aggregator.assert_metric(mname, tags=tags.METRIC_TAGS, at_least=0)

    # Compute match rate
    after = len(aggregator.not_asserted())

    assert before - after > at_least


@pytest.mark.unit
def test__get_server_pid():
    """
    Test the logic looping through the processes searching for `mysqld`
    """
    mysql_check = MySql(common.CHECK_NAME, {}, instances=[{'server': 'localhost', 'user': 'datadog'}])
    mysql_check._get_pid_file_variable = mock.MagicMock(return_value=None)
    mysql_check.log = mock.MagicMock()
    dummy_proc = subprocess.Popen(["python"])

    p_iter = psutil.process_iter

    def process_iter():
        """
        Wrap `psutil.process_iter` with a func killing a running process
        while iterating to reproduce a bug in the pid detection.
        We don't use psutil directly here because at the time this will be
        invoked, `psutil.process_iter` will be mocked. Instead we assign it to
        `p_iter` which is then part of the closure (see line above).
        """
        for p in p_iter():
            if dummy_proc.pid == p.pid:
                dummy_proc.terminate()
                dummy_proc.wait()
            # continue as the original `process_iter` function
            yield p

    with mock.patch('datadog_checks.mysql.mysql.psutil.process_iter', process_iter):
        with mock.patch('datadog_checks.mysql.mysql.PROC_NAME', 'this_shouldnt_exist'):
            # the pid should be none but without errors
            assert mysql_check._get_server_pid(None) is None
            assert mysql_check.log.exception.call_count == 0


@pytest.mark.unit
def test_parse_get_version():
    class MockCursor:
        version = (b'5.5.12-log',)

        def execute(self, command):
            pass

        def close(self):
            return MockCursor()

        def fetchone(self):
            return self.version

    class MockDatabase:
        def cursor(self):
            return MockCursor()

    mocked_db = MockDatabase()
    for mocked_db.version in [(b'5.5.12-log',), ('5.5.12-log',)]:
        v = get_version(mocked_db)
        assert v.version == '5.5.12'
        assert v.flavor == 'MySQL'
        assert v.build == 'log'


@pytest.mark.integration
@pytest.mark.usefixtures('dd_environment')
def test_version_metadata(instance_basic, datadog_agent, version_metadata):
    mysql_check = MySql(common.CHECK_NAME, {}, instances=[instance_basic])
    mysql_check.check_id = 'test:123'

    mysql_check.check(instance_basic)
    datadog_agent.assert_metadata('test:123', version_metadata)
    datadog_agent.assert_metadata_count(len(version_metadata))


@pytest.mark.integration
@pytest.mark.usefixtures('dd_environment')
def test_custom_queries(aggregator, instance_custom_queries, dd_run_check):
    mysql_check = MySql(common.CHECK_NAME, {}, instances=[instance_custom_queries])
    dd_run_check(mysql_check)

    aggregator.assert_metric('alice.age', value=25, tags=tags.METRIC_TAGS)
    aggregator.assert_metric('bob.age', value=20, tags=tags.METRIC_TAGS)
