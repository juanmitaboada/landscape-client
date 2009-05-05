"""Provide access to the persistent data used by L{PackageTaskHandler}s."""
import time

try:
    import sqlite3
except ImportError:
    from pysqlite2 import dbapi2 as sqlite3

from landscape.lib import bpickle


class UnknownHashIDRequest(Exception):
    """Raised for unknown hash id requests."""


class InvalidHashIdDb(Exception):
    """Raised when trying to add an invalid hash=>id lookaside database."""


def with_cursor(method):
    """Decorator that encloses the method in a database transaction.

    Even though SQLite is supposed to be useful in autocommit mode, we've
    found cases where the database continued to be locked for writing
    until the cursor was closed.  With this in mind, instead of using
    the autocommit mode, we explicitly terminate transactions and enforce
    cursor closing with this decorator.
    """

    def inner(self, *args, **kwargs):
        try:
            cursor = self._db.cursor()
            try:
                result = method(self, cursor, *args, **kwargs)
            finally:
                cursor.close()
            self._db.commit()
        except:
            self._db.rollback()
            raise
        return result
    return inner


class HashIdStore(object):
    """Persist package hash=>id mappings in a file.

    The implementation uses a SQLite database as backend, with a single
    table called "hash", whose schema is defined in L{ensure_hash_id_schema}.
    """

    def __init__(self, filename):
        """
        @param filename: The file where the mappings are persisted to.
        """
        self._filename = filename
        self._db = sqlite3.connect(self._filename)
        ensure_hash_id_schema(self._db)

    @with_cursor
    def set_hash_ids(self, cursor, hash_ids):
        """Set the ids of a set of hashes.

        @param hash_ids: a C{dict} of hash=>id mappings.
        """
        for hash, id in hash_ids.iteritems():
            cursor.execute("REPLACE INTO hash VALUES (?, ?)",
                           (id, buffer(hash)))

    @with_cursor
    def get_hash_id(self, cursor, hash):
        """Return the id associated to C{hash}, or C{None} if not available."""
        cursor.execute("SELECT id FROM hash WHERE hash=?", (buffer(hash),))
        value = cursor.fetchone()
        if value:
            return value[0]
        return None

    @with_cursor
    def get_hash_ids(self, cursor):
        """Return a C{dict} holding all the available hash=>id mappings."""
        cursor.execute("SELECT hash, id FROM hash")
        return dict([(str(row[0]), row[1]) for row in cursor.fetchall()])

    @with_cursor
    def get_id_hash(self, cursor, id):
        """Return the hash associated to C{id}, or C{None} if not available."""
        assert isinstance(id, (int, long))
        cursor.execute("SELECT hash FROM hash WHERE id=?", (id,))
        value = cursor.fetchone()
        if value:
            return str(value[0])
        return None

    @with_cursor
    def clear_hash_ids(self, cursor):
        """Delete all hash=>id mappings."""
        cursor.execute("DELETE FROM hash")

    @with_cursor
    def check_sanity(self, cursor):
        """Check database integrity.

        @raise: L{InvalidHashIdDb} if the filenme passed to the constructor is
            not a SQLite database or does not have a table called "hash" with
            a compatible schema.
        """
        try:
            cursor.execute("SELECT id FROM hash WHERE hash=?", ("",))
        except sqlite3.DatabaseError, e:
            raise InvalidHashIdDb(self._filename)


class PackageStore(HashIdStore):
    """Persist data about system packages and L{PackageTaskHandler}'s tasks.

    This class extends L{HashIdStore} by adding tables to the SQLite database
    backend for storing information about the status of the system packages and
    about the tasks to be performed by L{PackageTaskHandler}s.

    The additional tables and schemas are defined in L{ensure_package_schema}.
    """

    def __init__(self, filename):
        """
        @param filename: The file where data is persisted to.
        """
        super(PackageStore, self).__init__(filename)
        self._hash_id_stores = []
        ensure_package_schema(self._db)

    def add_hash_id_db(self, filename):
        """
        Attach an additional "lookaside" hash=>id database.

        This method can be called more than once to attach several
        hash=>id databases, which will be queried *before* the main
        database, in the same the order they were added.

        If C{filename} is not a SQLite database or does not have a
        table called "hash" with a compatible schema, L{InvalidHashIdDb}
        is raised.

        @param filename: a secondary SQLite databases to look for pre-canned
                         hash=>id mappings.
        """
        hash_id_store = HashIdStore(filename)

        try:
            hash_id_store.check_sanity()
        except InvalidHashIdDb, e:
            # propagate the error
            raise e

        self._hash_id_stores.append(hash_id_store)

    def has_hash_id_db(self):
        """Return C{True} if one or more lookaside databases are attached."""
        return len(self._hash_id_stores) > 0

    def get_hash_id(self, hash):
        """Return the id associated to C{hash}, or C{None} if not available.

        This method composes the L{HashIdStore.get_hash_id} methods of all
        the attached lookaside databases, falling back to the main one, as
        described in L{add_hash_id_db}.
        """
        assert isinstance(hash, basestring)

        # Check if we can find the hash=>id mapping in the lookaside stores
        for store in self._hash_id_stores:
            id = store.get_hash_id(hash)
            if id:
                return id

        # Fall back to the locally-populated db
        return HashIdStore.get_hash_id(self, hash)

    @with_cursor
    def add_available(self, cursor, ids):
        for id in ids:
            cursor.execute("REPLACE INTO available VALUES (?)", (id,))

    @with_cursor
    def remove_available(self, cursor, ids):
        id_list = ",".join(str(int(id)) for id in ids)
        cursor.execute("DELETE FROM available WHERE id IN (%s)" % id_list)

    @with_cursor
    def clear_available(self, cursor):
        cursor.execute("DELETE FROM available")

    @with_cursor
    def get_available(self, cursor):
        cursor.execute("SELECT id FROM available")
        return [row[0] for row in cursor.fetchall()]

    @with_cursor
    def add_available_upgrades(self, cursor, ids):
        for id in ids:
            cursor.execute("REPLACE INTO available_upgrade VALUES (?)", (id,))

    @with_cursor
    def remove_available_upgrades(self, cursor, ids):
        id_list = ",".join(str(int(id)) for id in ids)
        cursor.execute("DELETE FROM available_upgrade WHERE id IN (%s)"
                       % id_list)

    @with_cursor
    def clear_available_upgrades(self, cursor):
        cursor.execute("DELETE FROM available_upgrade")

    @with_cursor
    def get_available_upgrades(self, cursor):
        cursor.execute("SELECT id FROM available_upgrade")
        return [row[0] for row in cursor.fetchall()]

    @with_cursor
    def add_installed(self, cursor, ids):
        for id in ids:
            cursor.execute("REPLACE INTO installed VALUES (?)", (id,))

    @with_cursor
    def remove_installed(self, cursor, ids):
        id_list = ",".join(str(int(id)) for id in ids)
        cursor.execute("DELETE FROM installed WHERE id IN (%s)" % id_list)

    @with_cursor
    def clear_installed(self, cursor):
        cursor.execute("DELETE FROM installed")

    @with_cursor
    def get_installed(self, cursor):
        cursor.execute("SELECT id FROM installed")
        return [row[0] for row in cursor.fetchall()]

    @with_cursor
    def add_hash_id_request(self, cursor, hashes):
        hashes = list(hashes)
        cursor.execute("INSERT INTO hash_id_request (hashes, timestamp)"
                       " VALUES (?,?)",
                       (buffer(bpickle.dumps(hashes)), time.time()))
        return HashIDRequest(self._db, cursor.lastrowid)

    @with_cursor
    def get_hash_id_request(self, cursor, request_id):
        cursor.execute("SELECT 1 FROM hash_id_request WHERE id=?",
                       (request_id,))
        if not cursor.fetchone():
            raise UnknownHashIDRequest(request_id)
        return HashIDRequest(self._db, request_id)

    @with_cursor
    def iter_hash_id_requests(self, cursor):
        cursor.execute("SELECT id FROM hash_id_request")
        for row in cursor.fetchall():
            yield HashIDRequest(self._db, row[0])

    @with_cursor
    def clear_hash_id_requests(self, cursor):
        cursor.execute("DELETE FROM hash_id_request")

    @with_cursor
    def add_task(self, cursor, queue, data):
        data = bpickle.dumps(data)
        cursor.execute("INSERT INTO task (queue, timestamp, data) "
                       "VALUES (?,?,?)", (queue, time.time(), buffer(data)))
        return PackageTask(self._db, cursor.lastrowid)

    @with_cursor
    def get_next_task(self, cursor, queue):
        cursor.execute("SELECT id FROM task WHERE queue=? ORDER BY timestamp",
                       (queue,))
        row = cursor.fetchone()
        if row:
            return PackageTask(self._db, row[0])
        return None

    @with_cursor
    def clear_tasks(self, cursor, except_tasks=()):
        cursor.execute("DELETE FROM task WHERE id NOT IN (%s)" %
                       ",".join([str(task.id) for task in except_tasks]))

    @with_cursor
    def get_flag(self, cursor, name):
        """Return the value of the boolean flag labelled as C{name}.

        @note: If the flag does not exist, C{False} is returned.
        """
        cursor.execute("SELECT value FROM flag WHERE name=?", (name,))
        value = cursor.fetchone()
        if value:
            return value[0]
        return False

    @with_cursor
    def set_flag(self, cursor, name, value):
        """Set the boolean flag labelled as C{name} to C{value}."""
        cursor.execute("REPLACE INTO flag VALUES (?, ?)", (name, value))


class HashIDRequest(object):

    def __init__(self, db, id):
        self._db = db
        self.id = id

    @property
    @with_cursor
    def hashes(self, cursor):
        cursor.execute("SELECT hashes FROM hash_id_request WHERE id=?",
                       (self.id,))
        return bpickle.loads(str(cursor.fetchone()[0]))

    @with_cursor
    def _get_timestamp(self, cursor):
        cursor.execute("SELECT timestamp FROM hash_id_request WHERE id=?",
                       (self.id,))
        return cursor.fetchone()[0]

    @with_cursor
    def _set_timestamp(self, cursor, value):
        cursor.execute("UPDATE hash_id_request SET timestamp=? WHERE id=?",
                       (value, self.id))

    timestamp = property(_get_timestamp, _set_timestamp)

    @with_cursor
    def _get_message_id(self, cursor):
        cursor.execute("SELECT message_id FROM hash_id_request WHERE id=?",
                       (self.id,))
        return cursor.fetchone()[0]

    @with_cursor
    def _set_message_id(self, cursor, value):
        cursor.execute("UPDATE hash_id_request SET message_id=? WHERE id=?",
                       (value, self.id))

    message_id = property(_get_message_id, _set_message_id)

    @with_cursor
    def remove(self, cursor):
        cursor.execute("DELETE FROM hash_id_request WHERE id=?", (self.id,))


class PackageTask(object):

    def __init__(self, db, id):
        self._db = db
        self.id = id

        cursor = db.cursor()
        try:
            cursor.execute("SELECT queue, timestamp, data FROM task "
                           "WHERE id=?", (id,))
            row = cursor.fetchone()
        finally:
            cursor.close()

        self.queue = row[0]
        self.timestamp = row[1]
        self.data = bpickle.loads(str(row[2]))

    @with_cursor
    def remove(self, cursor):
        cursor.execute("DELETE FROM task WHERE id=?", (self.id,))


def ensure_hash_id_schema(db):
    """Create all tables needed by a L{HashIdStore}.

    @param db: A connection to a SQLite database.
    """
    cursor = db.cursor()
    try:
        cursor.execute("CREATE TABLE hash"
                       " (id INTEGER PRIMARY KEY, hash BLOB UNIQUE)")
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        cursor.close()
        db.rollback()
    else:
        cursor.close()
        db.commit()


def ensure_package_schema(db):
    """Create all tables needed by a L{PackageStore}.

    @param db: A connection to a SQLite database.
    """
    # FIXME This needs a "patch" table with a "version" column which will
    #       help with upgrades.  It should also be used to decide when to
    #       create the schema from the ground up, rather than that using
    #       try block.
    cursor = db.cursor()
    try:
        cursor.execute("CREATE TABLE flag"
                       " (name TEXT PRIMARY KEY,"
                       " value BOOLEAN)")
        cursor.execute("CREATE TABLE available"
                       " (id INTEGER PRIMARY KEY)")
        cursor.execute("CREATE TABLE available_upgrade"
                       " (id INTEGER PRIMARY KEY)")
        cursor.execute("CREATE TABLE installed"
                       " (id INTEGER PRIMARY KEY)")
        cursor.execute("CREATE TABLE hash_id_request"
                       " (id INTEGER PRIMARY KEY, timestamp TIMESTAMP,"
                       " message_id INTEGER, hashes BLOB)")
        cursor.execute("CREATE TABLE task"
                       " (id INTEGER PRIMARY KEY, queue TEXT,"
                       " timestamp TIMESTAMP, data BLOB)")
    except sqlite3.OperationalError:
        cursor.close()
        db.rollback()
    else:
        cursor.close()
        db.commit()
