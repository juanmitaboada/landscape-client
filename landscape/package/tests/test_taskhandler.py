import os

from twisted.internet import reactor
from twisted.internet.defer import Deferred, fail

from landscape.lib.lock import lock_path
from landscape.lib.command import CommandError

from landscape.broker.remote import RemoteBroker

from landscape.package.taskhandler import (
    PackageTaskHandlerConfiguration, PackageTaskHandler, run_task_handler)
from landscape.package.facade import SmartFacade
from landscape.package.store import HashIdStore, PackageStore
from landscape.package.tests.helpers import SmartFacadeHelper

from landscape.tests.helpers import (
    LandscapeIsolatedTest, RemoteBrokerHelper)
from landscape.tests.mocker import ANY, ARGS, MATCH


def ISTYPE(match_type):
    return MATCH(lambda arg: type(arg) is match_type)


SAMPLE_LSB_RELEASE = "DISTRIB_CODENAME=codename\n"

class PackageTaskHandlerTest(LandscapeIsolatedTest):

    helpers = [SmartFacadeHelper, RemoteBrokerHelper]

    def setUp(self):
        super(PackageTaskHandlerTest, self).setUp()

        self.config = PackageTaskHandlerConfiguration()
        self.store = PackageStore(self.makeFile())
        self.handler = PackageTaskHandler(self.store, self.facade, self.remote,
                                          self.config)

    def test_ensure_channels_reloaded(self):
        self.assertEquals(len(self.facade.get_packages()), 0)
        self.handler.ensure_channels_reloaded()
        self.assertEquals(len(self.facade.get_packages()), 3)

        # Calling it once more won't reload channels again.
        self.facade.get_packages_by_name("name1")[0].installed = True
        self.handler.ensure_channels_reloaded()
        self.assertTrue(self.facade.get_packages_by_name("name1")[0].installed)

    def test_use_hash_id_db(self):

        # We don't have this hash=>id mapping
        self.assertEquals(self.store.get_hash_id("hash"), None)

        # An appropriate hash=>id database is available
        self.config.data_path = self.makeDir()
        os.makedirs(os.path.join(self.config.data_path, "package", "hash-id"))
        hash_id_db_filename = os.path.join(self.config.data_path, "package",
                                           "hash-id", "uuid_codename_arch")
        HashIdStore(hash_id_db_filename).set_hash_ids({"hash": 123})

        # Fake uuid, codename and arch
        message_store = self.broker_service.message_store
        message_store.set_server_uuid("uuid")
        self.handler.lsb_release_filename = self.makeFile(SAMPLE_LSB_RELEASE)
        self.facade.set_arch("arch")

        # Attach the hash=>id database to our store
        self.mocker.replay()
        result = self.handler.use_hash_id_db()

        # Now we do have the hash=>id mapping
        def callback(ignored):
            self.assertEquals(self.store.get_hash_id("hash"), 123)
        result.addCallback(callback)

        return result

    def test_use_hash_id_db_undetermined_codename(self):

        # Fake uuid
        message_store = self.broker_service.message_store
        message_store.set_server_uuid("uuid")

        # Undetermined codename
        self.handler.lsb_release_filename = self.makeFile("Foo=bar")

        # The failure should be properly logged
        logging_mock = self.mocker.replace("logging.warning")
        logging_mock("Couldn't determine which hash=>id database to use: "
                     "missing code-name key in %s" %
                     self.handler.lsb_release_filename)
        self.mocker.result(None)

        # Go!
        self.mocker.replay()
        result = self.handler.use_hash_id_db()
        return result

    def test_wb_determine_hash_id_db_filename_server_uuid_is_none(self):
        """
        The L{PaclageTaskHandler._determine_hash_id_db_filename} method should
        return C{None} if the server uuid is C{None}.
        """
        message_store = self.broker_service.message_store
        message_store.set_server_uuid(None)

        result = self.handler._determine_hash_id_db_filename()
        def callback(hash_id_db_filename):
            self.assertIs(hash_id_db_filename, None)
        result.addCallback(callback)
        return result

    def test_use_hash_id_db_undetermined_server_uuid(self):
        """
        If the server-uuid can't be determined for some reason, no hash-id db
        should be used and the failure should be properly logged.
        """
        message_store = self.broker_service.message_store
        message_store.set_server_uuid(None)

        logging_mock = self.mocker.replace("logging.warning")
        logging_mock("Couldn't determine which hash=>id database to use: "
                     "server UUID not available")
        self.mocker.result(None)
        self.mocker.replay()

        result = self.handler.use_hash_id_db()
        def callback(ignore):
            self.assertFalse(self.store.has_hash_id_db())
        result.addCallback(callback)
        return result

    def test_use_hash_id_db_undetermined_arch(self):

        # Fake uuid and codename
        message_store = self.broker_service.message_store
        message_store.set_server_uuid("uuid")
        self.handler.lsb_release_filename = self.makeFile(SAMPLE_LSB_RELEASE)

        # Undetermined arch
        self.facade.set_arch(None)

        # The failure should be properly logged
        logging_mock = self.mocker.replace("logging.warning")
        logging_mock("Couldn't determine which hash=>id database to use: "\
                     "unknown dpkg architecture")
        self.mocker.result(None)

        # Go!
        self.mocker.replay()
        result = self.handler.use_hash_id_db()

        return result

    def test_use_hash_id_db_database_not_found(self):

        # Clean path, we don't have an appropriate hash=>id database
        self.config.data_path = self.makeDir()

        # Fake uuid, codename and arch
        message_store = self.broker_service.message_store
        message_store.set_server_uuid("uuid")
        self.handler.lsb_release_filename = self.makeFile(SAMPLE_LSB_RELEASE)
        self.facade.set_arch("arch")

        # Let's try
        self.mocker.replay()
        result = self.handler.use_hash_id_db()

        # We go on without the hash=>id database
        def callback(ignored):
            self.assertFalse(self.store.has_hash_id_db())
        result.addCallback(callback)

        return result

    def test_use_hash_id_with_invalid_database(self):

        # Let's say the appropriate database is actually garbage
        self.config.data_path = self.makeDir()
        os.makedirs(os.path.join(self.config.data_path, "package", "hash-id"))
        hash_id_db_filename = os.path.join(self.config.data_path, "package",
                                           "hash-id", "uuid_codename_arch")
        open(hash_id_db_filename, "w").write("junk")

        # Fake uuid, codename and arch
        message_store = self.broker_service.message_store
        message_store.set_server_uuid("uuid")
        self.handler.lsb_release_filename = self.makeFile(SAMPLE_LSB_RELEASE)
        self.facade.set_arch("arch")

        # The failure should be properly logged
        logging_mock = self.mocker.replace("logging.warning")
        logging_mock("Invalid hash=>id database %s" % hash_id_db_filename)
        self.mocker.result(None)

        # Try to attach it
        self.mocker.replay()
        result = self.handler.use_hash_id_db()

        # We remove the broken hash=>id database and go on without it
        def callback(ignored):
            self.assertFalse(os.path.exists(hash_id_db_filename))
            self.assertFalse(self.store.has_hash_id_db())
        result.addCallback(callback)

        return result

    def test_run(self):
        handler_mock = self.mocker.patch(self.handler)
        handler_mock.handle_tasks()
        self.mocker.result("WAYO!")

        self.mocker.replay()

        self.assertEquals(self.handler.run(), "WAYO!")

    def test_handle_tasks(self):
        queue_name = PackageTaskHandler.queue_name

        self.store.add_task(queue_name, 0)
        self.store.add_task(queue_name, 1)
        self.store.add_task(queue_name, 2)

        results = [Deferred() for i in range(3)]

        stash = []
        def handle_task(task):
            result = results[task.data]
            result.addCallback(lambda x: stash.append(task.data))
            return result

        handler_mock = self.mocker.patch(self.handler)
        handler_mock.handle_task(ANY)
        self.mocker.call(handle_task)
        self.mocker.count(3)
        self.mocker.replay()

        handle_tasks_result = self.handler.handle_tasks()

        self.assertEquals(stash, [])

        results[1].callback(None)
        self.assertEquals(stash, [])
        self.assertEquals(self.store.get_next_task(queue_name).data, 0)

        results[0].callback(None)
        self.assertEquals(stash, [0, 1])
        self.assertTrue(handle_tasks_result.called)
        self.assertEquals(self.store.get_next_task(queue_name).data, 2)

        results[2].callback(None)
        self.assertEquals(stash, [0, 1, 2])
        self.assertTrue(handle_tasks_result.called)
        self.assertEquals(self.store.get_next_task(queue_name), None)

        handle_tasks_result = self.handler.handle_tasks()
        self.assertTrue(handle_tasks_result.called)

    def test_handle_tasks_hooks_errback(self):
        queue_name = PackageTaskHandler.queue_name

        self.store.add_task(queue_name, 0)

        class MyException(Exception): pass

        def handle_task(task):
            result = Deferred()
            result.errback(MyException())
            return result

        handler_mock = self.mocker.patch(self.handler)
        handler_mock.handle_task(ANY)
        self.mocker.call(handle_task)
        self.mocker.replay()

        stash = []
        handle_tasks_result = self.handler.handle_tasks()
        handle_tasks_result.addErrback(stash.append)

        self.assertEquals(len(stash), 1)
        self.assertEquals(stash[0].type, MyException)

    def test_default_handle_task(self):
        result = self.handler.handle_task(None)
        self.assertTrue(isinstance(result, Deferred))
        self.assertTrue(result.called)

    def test_run_task_handler(self):

        # This is a slightly lengthy one, so bear with me.

        data_path = self.makeDir()

        # Prepare the mock objects.
        lock_path_mock = self.mocker.replace("landscape.lib.lock.lock_path",
                                             passthrough=False)
        install_mock = self.mocker.replace("twisted.internet."
                                           "glib2reactor.install")
        reactor_mock = self.mocker.replace("twisted.internet.reactor",
                                           passthrough=False)
        init_logging_mock = self.mocker.replace("landscape.deployment"
                                                ".init_logging",
                                                passthrough=False)
        HandlerMock = self.mocker.proxy(PackageTaskHandler)

        # The goal of this method is to perform a sequence of tasks
        # where the ordering is important.
        self.mocker.order()

        # As the very first thing, install the twisted glib2 reactor
        # so that we can use DBUS safely.
        install_mock()

        # Then, we must acquire a lock as the same task handler should
        # never have two instances running in parallel.  The 'default'
        # below comes from the queue_name attribute.
        lock_path_mock(os.path.join(data_path, "package", "default.lock"))

        # Once locking is done, it's safe to start logging without
        # corrupting the file.  We don't want any output unless it's
        # breaking badly, so the quiet option should be set.
        init_logging_mock(ISTYPE(PackageTaskHandlerConfiguration),
                          "package-default")

        # Then, it must create an instance of the TaskHandler subclass
        # passed in as a parameter.  We'll keep track of the arguments
        # given and verify them later.
        handler_args = []
        handler_mock = HandlerMock(ANY, ANY, ANY, ANY)
        self.mocker.passthrough() # Let the real constructor run for testing.
        self.mocker.call(lambda *args: handler_args.extend(args))

        to_call = []
        reactor_mock.callWhenRunning(ANY)
        self.mocker.call(lambda callback: to_call.append(callback))

        # With all of that done, the Twisted reactor must be run, so that
        # deferred tasks are correctly performed.
        reactor_mock.run()

        self.mocker.unorder()

        # The following tasks are hooked in as callbacks of our deferred.
        # We must use callLater() so that stop() won't happen before run().
        reactor_mock.callLater(0, "STOP METHOD")
        reactor_mock.stop
        self.mocker.result("STOP METHOD")

        # We also expect the umask to be set appropriately before running the
        # commands
        umask = self.mocker.replace("os.umask")
        umask(022)

        # Okay, the whole playground is set.
        self.mocker.replay()

        try:
            # DO IT!
            result = run_task_handler(HandlerMock,
                                      ["--data-path", data_path,
                                       "--bus", "session"])

            # reactor.stop() wasn't run yet, so it must fail right now.
            self.assertRaises(AssertionError, self.mocker.verify)

            # DO THE REST OF IT! :-)
            to_call[0]()

            # Are we there yet!?
            self.mocker.verify()
        finally:
            # Put reactor back in place before returning.
            self.mocker.reset()

        store, facade, broker, config = handler_args

        # Verify if the arguments to the reporter constructor were correct.
        self.assertEquals(type(store), PackageStore)
        self.assertEquals(type(facade), SmartFacade)
        self.assertEquals(type(broker), RemoteBroker)
        self.assertEquals(type(config), PackageTaskHandlerConfiguration)

        # Let's see if the store path is where it should be.
        filename = os.path.join(data_path, "package", "database")
        store.add_available([1, 2, 3])
        other_store = PackageStore(filename)
        self.assertEquals(other_store.get_available(), [1, 2, 3])

        # Check the hash=>id database directory as well
        self.assertTrue(os.path.exists(os.path.join(data_path,
                                                    "package", "hash-id")))

    def test_run_task_handler_when_already_locked(self):
        data_path = self.makeDir()

        install_mock = self.mocker.replace("twisted.internet."
                                           "glib2reactor.install")
        install_mock()

        self.mocker.replay()

        os.mkdir(os.path.join(data_path, "package"))
        lock_path(os.path.join(data_path, "package", "default.lock"))

        try:
            run_task_handler(PackageTaskHandler, ["--data-path", data_path])
        except SystemExit, e:
            self.assertIn("default is already running", str(e))
        else:
            self.fail("SystemExit not raised")

    def test_run_task_handler_when_already_locked_and_quiet_option(self):
        data_path = self.makeDir()

        install_mock = self.mocker.replace("twisted.internet."
                                           "glib2reactor.install")
        install_mock()

        self.mocker.replay()

        os.mkdir(os.path.join(data_path, "package"))
        lock_path(os.path.join(data_path, "package", "default.lock"))

        try:
            run_task_handler(PackageTaskHandler,
                             ["--data-path", data_path, "--quiet"])
        except SystemExit, e:
            self.assertEquals(str(e), "")
        else:
            self.fail("SystemExit not raised")

    def test_errors_in_tasks_are_printed_and_exit_program(self):
        # Ignore a bunch of crap that we don't care about
        install_mock = self.mocker.replace("twisted.internet."
                                           "glib2reactor.install")
        install_mock()
        reactor_mock = self.mocker.proxy(reactor)
        init_logging_mock = self.mocker.replace("landscape.deployment"
                                                ".init_logging",
                                                passthrough=False)
        init_logging_mock(ARGS)
        reactor_mock.run()

        # Get a deferred which will fire when the reactor is stopped, so the
        # test runs until the reactor is stopped.
        done = Deferred()
        self.expect(reactor_mock.stop()).call(lambda: done.callback(None))

        class MyException(Exception): pass

        self.log_helper.ignore_errors(MyException)

        # Simulate a task handler which errors out.
        handler_factory_mock = self.mocker.proxy(PackageTaskHandler)
        handler_mock = handler_factory_mock(ARGS)
        self.expect(handler_mock.run()).result(fail(MyException("Hey error")))

        self.mocker.replay()

        # Ok now for some real stuff

        result = run_task_handler(handler_factory_mock,
                                  ["--data-path", self.data_path,
                                   "--bus", "session"],
                                  reactor=reactor_mock)

        def everything_stopped(result):
            self.assertIn("MyException", self.logfile.getvalue())

        return done.addCallback(everything_stopped)
