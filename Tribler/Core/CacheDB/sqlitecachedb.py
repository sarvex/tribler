# Written by Jie Yang
# see LICENSE.txt for license information
import logging
import os
from base64 import encodestring, decodestring
from threading import currentThread, RLock

import apsw
from apsw import CantOpenError, SQLError
from twisted.python.threadable import isInIOThread

from Tribler.dispersy.taskmanager import TaskManager
from Tribler.dispersy.util import blocking_call_on_reactor_thread, call_on_reactor_thread

from Tribler import LIBRARYNAME
from Tribler.Core.CacheDB.db_versions import LATEST_DB_VERSION


DB_SCRIPT_NAME = f"schema_sdb_v{str(LATEST_DB_VERSION)}.sql"
DB_SCRIPT_RELATIVE_PATH = os.path.join(LIBRARYNAME, DB_SCRIPT_NAME)

DB_FILE_NAME = u"tribler.sdb"
DB_DIR_NAME = u"sqlite"
DB_FILE_RELATIVE_PATH = os.path.join(DB_DIR_NAME, DB_FILE_NAME)


DEFAULT_BUSY_TIMEOUT = 10000

TRHEADING_DEBUG = False

forceDBThread = call_on_reactor_thread
forceAndReturnDBThread = blocking_call_on_reactor_thread


class CorruptedDatabaseError(Exception):
    pass


def bin2str(bin_data):
    return encodestring(bin_data).replace("\n", "")


def str2bin(str_data):
    return decodestring(str_data)


class SQLiteCacheDB(TaskManager):

    def __init__(self, db_path, db_script_path=None, busytimeout=DEFAULT_BUSY_TIMEOUT):
        super(SQLiteCacheDB, self).__init__()

        self._logger = logging.getLogger(self.__class__.__name__)

        self._cursor_lock = RLock()
        self._cursor_table = {}

        self._connection = None
        self.sqlite_db_path = db_path
        self.db_script_path = db_script_path
        self._busytimeout = busytimeout  # busytimeout is in milliseconds

        self._version = None

        self._should_commit = False
        self._show_execute = False

    @property
    def version(self):
        """The version of this database."""
        return self._version

    @blocking_call_on_reactor_thread
    def initialize(self):
        """ Initializes the database. If the database doesn't exist, we create a new one. Otherwise, we check the
            version and upgrade to the latest version.
        """

        # open a connection to the database
        self._open_connection()

    @blocking_call_on_reactor_thread
    def close(self):
        self.cancel_all_pending_tasks()
        with self._cursor_lock:
            for cursor in self._cursor_table.itervalues():
                cursor.close()
            self._cursor_table = {}
            self._connection.close()
            self._connection = None

    def _open_connection(self):
        """ Opens a connection to the database. If the database doesn't exist, we create a new one and run the
            initialization SQL scripts. If the database doesn't exist, we simply connect to it.
            And finally, we read the database version.
        """
        # check if it is in memory
        is_in_memory = self.sqlite_db_path == u":memory:"
        is_new_db = is_in_memory

        if not os.path.exists(self.sqlite_db_path):
            if not is_new_db:
                # create a new one
                is_new_db = True
        elif not os.path.isfile(self.sqlite_db_path):
            if not is_new_db:
                msg = f"Not a file: {self.sqlite_db_path}"
                raise OSError(msg)

        # create connection
        try:
            self._connection = apsw.Connection(self.sqlite_db_path)
            self._connection.setbusytimeout(self._busytimeout)
        except CantOpenError as e:
            msg = f"Failed to open connection to {self.sqlite_db_path}: {e}"
            raise CantOpenError(msg)

        cursor = self.get_cursor()

        # apply pragma
        page_size, = next(cursor.execute(u"PRAGMA page_size"))
        if page_size < 8192:
            # journal_mode and page_size only need to be set once.  because of the VACUUM this
            # is very expensive
            self._logger.info(u"begin page_size upgrade...")
            cursor.execute(u"PRAGMA journal_mode = DELETE;")
            cursor.execute(u"PRAGMA page_size = 8192;")
            cursor.execute(u"VACUUM;")
            self._logger.info(u"...end page_size upgrade")

        # http://www.sqlite.org/pragma.html
        # When synchronous is NORMAL, the SQLite database engine will still
        # pause at the most critical moments, but less often than in FULL
        # mode. There is a very small (though non-zero) chance that a power
        # failure at just the wrong time could corrupt the database in
        # NORMAL mode. But in practice, you are more likely to suffer a
        # catastrophic disk failure or some other unrecoverable hardware
        # fault.
        #
        cursor.execute(u"PRAGMA synchronous = NORMAL;")
        cursor.execute(u"PRAGMA cache_size = 10000;")

        # Niels 19-09-2012: even though my database upgraded to increase the pagesize it did not keep wal mode?
        # Enabling WAL on every starup
        cursor.execute(u"PRAGMA journal_mode = WAL;")

        # create tables if this is a new database
        if is_new_db and self.db_script_path is not None:
            self._logger.info(u"Initializing new database...")
            # check if the SQL script exists
            if not os.path.exists(self.db_script_path):
                msg = f"SQL script doesn't exist: {self.db_script_path}"
                raise OSError(msg)
            if not os.path.isfile(self.db_script_path):
                msg = f"SQL script is not a file: {self.db_script_path}"
                raise OSError(msg)

            try:
                with open(self.db_script_path, "r") as f:
                    sql_script = f.read()
            except IOError as e:
                msg = f"Failed to load SQL script {self.db_script_path}: {e}"
                raise IOError(msg)

            cursor.execute(sql_script)

        if self.db_script_path is not None:
            # read database version
            self._logger.info(u"Reading database version...")
            try:
                version_str, = cursor.execute(u"SELECT value FROM MyInfo WHERE entry == 'version'").next()
                self._version = int(version_str)
                self._logger.info(u"Current database version is %s", self._version)
            except (StopIteration, SQLError) as e:
                msg = f"Failed to load database version: {e}"
                raise CorruptedDatabaseError(msg)
        else:
            self._version = 1

    def get_cursor(self):
        thread_name = currentThread().getName()

        with self._cursor_lock:
            if thread_name not in self._cursor_table:
                self._cursor_table[thread_name] = self._connection.cursor()
            return self._cursor_table[thread_name]

    @blocking_call_on_reactor_thread
    def initial_begin(self):
        try:
            self._logger.info(u"Beginning the first transaction...")
            self.execute(u"BEGIN;")

        except:
            self._logger.exception(u"Failed to begin the first transaction")
            raise
        self._should_commit = False

    @blocking_call_on_reactor_thread
    def write_version(self, version):
        assert isinstance(
            version, int
        ), f"Invalid version type: {type(version)} is not int"
        assert (
            version <= LATEST_DB_VERSION
        ), f"Invalid version value: {version} > the latest {LATEST_DB_VERSION}"

        sql = u"UPDATE MyInfo SET value = ? WHERE entry == 'version'"
        self.execute_write(sql, (version,))
        self.commit_now()
        self._version = version

    @call_on_reactor_thread
    def commit_now(self, vacuum=False, exiting=False):
        if self._should_commit and isInIOThread():
            try:
                self._logger.info(u"Start committing...")
                self.execute(u"COMMIT;")
            except:
                self._logger.exception(u"COMMIT FAILED")
                raise
            self._should_commit = False

            if vacuum:
                self._logger.info(u"Start vacuuming...")
                self.execute(u"VACUUM;")

            if not exiting:
                try:
                    self._logger.info(u"Beginning another transaction...")
                    self.execute(u"BEGIN;")
                except:
                    self._logger.exception(u"Failed to execute BEGIN")
                    raise
            else:
                self._logger.info(u"Exiting, not beginning another transaction")

        elif vacuum:
            self.execute(u"VACUUM;")

    def clean_db(self, vacuum=False, exiting=False):
        self.execute_write(u"DELETE FROM TorrentFiles WHERE torrent_id IN (SELECT torrent_id FROM CollectedTorrent)")
        self.execute_write(u"DELETE FROM Torrent WHERE name IS NULL"
                           u" AND torrent_id NOT IN (SELECT torrent_id FROM _ChannelTorrents)")

        if vacuum:
            self.commit_now(vacuum, exiting=exiting)

    def set_show_sql(self, switch):
        self._show_execute = switch

    # --------- generic functions -------------

    @blocking_call_on_reactor_thread
    def execute(self, sql, args=None):
        cur = self.get_cursor()

        if self._show_execute:
            thread_name = currentThread().getName()
            self._logger.info(u"===%s===\n%s\n-----\n%s\n======\n", thread_name, sql, args)

        try:
            return cur.execute(sql) if args is None else cur.execute(sql, args)
        except Exception as msg:
            if str(msg).startswith(u"BusyError"):
                self._logger.error(u"cachedb: busylock error")

            else:
                thread_name = currentThread().getName()
                self._logger.exception(u"cachedb: ===%s===\nSQL Type: %s\n-----\n%s\n-----\n%s\n======\n",
                                       thread_name, type(sql), sql, args)

            raise msg

    @blocking_call_on_reactor_thread
    def executemany(self, sql, args=None):
        self._should_commit = True

        cur = self.get_cursor()
        if self._show_execute:
            thread_name = currentThread().getName()
            self._logger.info(u"===%s===\n%s\n-----\n%s\n======\n", thread_name, sql, args)

        try:
            return cur.executemany(sql) if args is None else cur.executemany(sql, args)
        except Exception as msg:
            thread_name = currentThread().getName()
            self._logger.exception(u"===%s===\nSQL Type: %s\n-----\n%s\n-----\n%s\n======\n",
                                   thread_name, type(sql), sql, args)
            raise msg

    def execute_read(self, sql, args=None):
        return self.execute(sql, args)

    def execute_write(self, sql, args=None):
        self._should_commit = True

        self.execute(sql, args)

    def insert_or_ignore(self, table_name, **argv):
        if len(argv) == 1:
            sql = f'INSERT OR IGNORE INTO {table_name} ({argv.keys()[0]}) VALUES (?);'
        else:
            questions = '?,' * len(argv)
            sql = f'INSERT OR IGNORE INTO {table_name} {tuple(argv.keys())} VALUES ({questions[:-1]});'
        self.execute_write(sql, argv.values())

    def insert(self, table_name, **argv):
        if len(argv) == 1:
            sql = f'INSERT INTO {table_name} ({argv.keys()[0]}) VALUES (?);'
        else:
            questions = '?,' * len(argv)
            sql = f'INSERT INTO {table_name} {tuple(argv.keys())} VALUES ({questions[:-1]});'
        self.execute_write(sql, argv.values())

    # TODO: may remove this, only used by test_sqlitecachedb.py
    def insertMany(self, table_name, values, keys=None):
        """ values must be a list of tuples """

        questions = u'?,' * len(values[0])
        if keys is None:
            sql = f'INSERT INTO {table_name} VALUES ({questions[:-1]});'
        else:
            sql = f'INSERT INTO {table_name} {tuple(keys)} VALUES ({questions[:-1]});'
        self.executemany(sql, values)

    def update(self, table_name, where=None, **argv):
        assert argv, 'NO VALUES TO UPDATE SPECIFIED'
        if argv:
            sql = f'UPDATE {table_name} SET '
            arg = []
            for k, v in argv.iteritems():
                if isinstance(v, tuple):
                    sql += f'{k} {v[0]} ?,'
                    arg.append(v[1])
                else:
                    sql += f'{k}=?,'
                    arg.append(v)
            sql = sql[:-1]
            if where is not None:
                sql += f' WHERE {where}'
            self.execute_write(sql, arg)

    def delete(self, table_name, **argv):
        sql = f'DELETE FROM {table_name} WHERE '
        arg = []
        for k, v in argv.iteritems():
            if isinstance(v, tuple):
                sql += f'{k} {v[0]} ? AND '
                arg.append(v[1])
            else:
                sql += f'{k}=? AND '
                arg.append(v)
        sql = sql[:-5]
        self.execute_write(sql, argv.values())

    # -------- Read Operations --------
    def size(self, table_name):
        num_rec_sql = f"SELECT count(*) FROM {table_name} LIMIT 1"
        return self.fetchone(num_rec_sql)

    @blocking_call_on_reactor_thread
    def fetchone(self, sql, args=None):
        find = self.execute_read(sql, args)
        if not find:
            return
        find = list(find)
        if find:
            if len(find) > 1:
                self._logger.debug(
                    u"FetchONE resulted in many more rows than one, consider putting a LIMIT 1 in the sql statement %s, %s", sql, len(find))
            find = find[0]
        else:
            return
        return find if len(find) > 1 else find[0]

    @blocking_call_on_reactor_thread
    def fetchall(self, sql, args=None):
        res = self.execute_read(sql, args)
        return list(res) if res is not None else []

    def getOne(self, table_name, value_name, where=None, conj=u"AND", **kw):
        """ value_name could be a string, a tuple of strings, or '*'
        """
        if isinstance(value_name, (tuple, list)):
            value_names = u",".join(value_name)
        else:
            value_names = value_name

        if isinstance(table_name, (tuple, list)):
            table_names = u",".join(table_name)
        else:
            table_names = table_name

        sql = f'SELECT {value_names} FROM {table_names}'

        if where or kw:
            sql += u' WHERE '
        if where:
            sql += where
            if kw:
                sql += f' {conj} '
        if kw:
            arg = []
            for k, v in kw.iteritems():
                if isinstance(v, tuple):
                    operator = v[0]
                    arg.append(v[1])
                else:
                    operator = "="
                    arg.append(v)
                sql += f' {k} {operator} ? '
                sql += conj
            sql = sql[:-len(conj)]
        else:
            arg = None

        # print >> sys.stderr, 'SQL: %s %s' % (sql, arg)
        return self.fetchone(sql, arg)

    def getAll(self, table_name, value_name, where=None, group_by=None, having=None, order_by=None, limit=None,
               offset=None, conj=u"AND", **kw):
        """ value_name could be a string, or a tuple of strings
            order by is represented as order_by
            group by is represented as group_by
        """
        if isinstance(value_name, (tuple, list)):
            value_names = u",".join(value_name)
        else:
            value_names = value_name

        if isinstance(table_name, (tuple, list)):
            table_names = u",".join(table_name)
        else:
            table_names = table_name

        sql = f'SELECT {value_names} FROM {table_names}'

        if where or kw:
            sql += u' WHERE '
        if where:
            sql += where
            if kw:
                sql += f' {conj} '
        if kw:
            arg = []
            for k, v in kw.iteritems():
                if isinstance(v, tuple):
                    operator = v[0]
                    arg.append(v[1])
                else:
                    operator = u"="
                    arg.append(v)

                sql += f' {k} {operator} ?'
                sql += conj
            sql = sql[:-len(conj)]
        else:
            arg = None

        if group_by is not None:
            sql += f' GROUP BY {group_by}'
        if having is not None:
            sql += f' HAVING {having}'
        if order_by is not None:
            # you should add desc after order_by to reversely sort, i.e, 'last_seen desc' as order_by
            sql += f' ORDER BY {order_by}'
        if limit is not None:
            sql += u' LIMIT %d' % limit
        if offset is not None:
            sql += u' OFFSET %d' % offset

        try:
            return self.fetchall(sql, arg) or []
        except Exception as msg:
            self._logger.exception(u"Wrong getAll sql statement: %s", sql)
            raise Exception(msg)
