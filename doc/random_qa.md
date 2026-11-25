Random QA script
-------------

This suite contains standalone stress and option-randomization tests. The helper
script `util/pxc_util.py` can start a 3-node cluster for manual testing. To
start a cluster with a custom option such as `--innodb-buffer-pool-size=128M`,
edit the `start_cluster()` call in `util/pxc_util.py`.

Encryption can be enabled with `--encryption-run`. Debug logging uses `--debug`.

Running tests
-------------

Run the full random_qa suite:

```bash
python3 qa_framework.py --suites=random_qa
```

Run individual tests:

```bash
python3 qa_framework.py --tests=random_qa.random_mysqld_option_test.py
python3 qa_framework.py --tests=random_qa.pstress_crash_recovery_qa.py
python3 qa_framework.py --tests=random_qa.pstress_random_qa.py
```

Start a cluster manually with the utility script:

```bash
python3 util/pxc_util.py --start --debug
```

Random mysqld option combinations
---------------------------------

`random_mysqld_option_test.py` uses [Hypothesis](https://hypothesis.readthedocs.io/)
to draw combinations of options from `conf/mysql_options_pxc80.txt` (only the
options the configured mysqld reports in `mysqld --verbose --help`). For each
combination it starts a 3-node cluster with the options in every node's
config, loads data (sysbench, prepared statements, random tables), compares
the nodes and shuts down. Every combination must satisfy:

1. no node crashes (no crash marker in any node's error log);
2. if the cluster starts, every node ends with the same data;
3. every node shuts down within the shutdown timeout;
4. if node1 starts with the options, the other nodes can join it.

A combination mysqld refuses at startup, or under which the data load fails
cleanly, is recorded with the first `[ERROR]` line as the reason but is not a
failure. When a combination fails, Hypothesis shrinks it to the smallest
combination that still fails and prints it ("Falsifying example" or "Failing
test case"). The failing example is saved in `.hypothesis/` and tried first
on the next run.

The test needs Hypothesis (`python3 -m pip install hypothesis`) and takes about
1-1.5 minutes per combination. Environment variables:

- `PXC_OPTION_EXAMPLES`: combinations to try (default 10).
- `PXC_OPTION_MAX`: most options in one combination (default 6).
- `PXC_OPTION_SEED`: a fixed seed, to repeat a run exactly.

`EXCLUDED_OPTIONS` in the test lists options left out of the draw and why
(options that make mysqld exit, debug-build options, options that disable
Galera's consistency guarantees). `KNOWN_ISSUES` lists option values that
currently fail, so the test keeps looking for new problems. Today that is
`character-set-filesystem` set to `utf16`, `utf16le`, `ucs2` or `utf32`:
mysqld starts, but xtrabackup fails as SST donor ("cannot my_setwd"), so no
node can join (seen on PXC 8.4.8 and 8.4.11-12).

Example output (PXC 8.4.8):

```
Trying options: character-set-filesystem=big5, innodb-flush-method=nosync, innodb-fsync-threshold=0, innodb-sched-priority-io=16, secondary-engine-cost-threshold=1048576
  -> refused at startup: node1: unknown variable 'innodb-sched-priority-io=16'.
Trying options: delay-key-write=0, external-locking=0, ngram=0, old-alter-table=1
  -> started, data loaded
Combinations tried (including shrinking): 20 {'refused at startup': 4, 'started, data loaded': 16}
```

Shrinking a failure to the option that causes it (before the
`character-set-filesystem` values were listed as a known issue):

```
Trying options: back-log=0, character-set-filesystem=utf16, delay-key-write=1, net-read-timeout=16, regexp-time-limit=1024, relay-log-purge=1
  -> node1 started but a node could not join: node1: xtrabackup finished with error: 1.  Check /dev/shm/qa_options/node1//innobackup.backup.log
Trying options: character-set-filesystem=utf16
  -> node1 started but a node could not join: node1: xtrabackup finished with error: 1.  Check /dev/shm/qa_options/node1//innobackup.backup.log
Trying options: delay-key-write=1, relay-log-purge=1
  -> started, data loaded
```

Random QA run log
--------------
```
$ python3 util/pxc_util.py --start --debug

13:26:04  Startup sanity check                                                                                [ ✓ ]
13:26:04  Configuration file creation                                                                         [ ✓ ]
/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17/bin/mysqld --no-defaults  --initialize-insecure  --basedir=/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17 --datadir=/dev/shm/qa/node1 > /dev/shm/qa/log/startup1.log 2>&1
/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17/bin/mysqld --no-defaults  --initialize-insecure  --basedir=/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17 --datadir=/dev/shm/qa/node2 > /dev/shm/qa/log/startup2.log 2>&1
/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17/bin/mysqld --no-defaults  --initialize-insecure  --basedir=/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17 --datadir=/dev/shm/qa/node3 > /dev/shm/qa/log/startup3.log 2>&1
13:26:10  Initializing cluster                                                                                [ ✓ ]
/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17/bin/mysqld --defaults-file=/dev/shm/qa/conf/node1.cnf --datadir=/dev/shm/qa/node1 --basedir=/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17 --innodb-buffer-pool-size=128M  --wsrep-provider=/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17/lib/libgalera_smm.so --wsrep-new-cluster --log-error=/dev/shm/qa/log/node1.err > /dev/shm/qa/log/node1.err 2>&1 &
/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17/bin/mysql --user=root --socket=/dev/shm/qa/node1/mysql.sock -Bse"delete from mysql.user where user='';" > /dev/null 2>&1
/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17/bin/mysqld --defaults-file=/dev/shm/qa/conf/node2.cnf --datadir=/dev/shm/qa/node2 --basedir=/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17 --innodb-buffer-pool-size=128M  --wsrep-provider=/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17/lib/libgalera_smm.so --log-error=/dev/shm/qa/log/node2.err > /dev/shm/qa/log/node2.err 2>&1 &
/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17/bin/mysql --user=root --socket=/dev/shm/qa/node2/mysql.sock -Bse"delete from mysql.user where user='';" > /dev/null 2>&1
/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17/bin/mysqld --defaults-file=/dev/shm/qa/conf/node3.cnf --datadir=/dev/shm/qa/node3 --basedir=/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17 --innodb-buffer-pool-size=128M  --wsrep-provider=/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17/lib/libgalera_smm.so --log-error=/dev/shm/qa/log/node3.err > /dev/shm/qa/log/node3.err 2>&1 &
/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17/bin/mysql --user=root --socket=/dev/shm/qa/node3/mysql.sock -Bse"delete from mysql.user where user='';" > /dev/null 2>&1
13:26:57  Cluster startup                                                                                     [ ✓ ]
13:26:57  Database connection                                                                                 [ ✓ ]
/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17/bin/mysql --user=root --socket=/dev/shm/qa/node1/mysql.sock -e'drop database if exists test ; create database test ;' > /dev/null 2>&1
13:26:57  PXC connection string                                                                               [ ✓ ]
	/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17/bin/mysql --user=root --socket=/dev/shm/qa/node1/mysql.sock
	/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17/bin/mysql --user=root --socket=/dev/shm/qa/node2/mysql.sock
	/dev/shm/qa/Percona-XtraDB-Cluster_8.0.26-16.1_Linux.x86_64.glibc2.17/bin/mysql --user=root --socket=/dev/shm/qa/node3/mysql.sock


$ python3 qa_framework.py --tests=random_qa.pstress_crash_recovery_qa.py
--------------------
PXC Random PSTRESS QA
--------------------
06:32:02  Startup sanity check                                                                                [ ✓ ]
06:32:02  Configuration file creation                                                                         [ ✓ ]
06:32:43  Initializing cluster                                                                                [ ✓ ]
06:33:42  Cluster startup                                                                                     [ ✓ ]
06:33:42  Database connection                                                                                 [ ✓ ]
06:33:42  PSTRESS RUN command : /home/vagrant/pstress/src/pstress-pxc --database=test --threads=10 --logdir=/dev/shm/qa/log --log-all-queries --log-failed-queries --user=root --socket=/dev/shm/qa/node1/mysql.sock --seed 1000 --tables 5 --records 10 --no-encryption --seconds 30 --grammar-file /home/vagrant/pstress/src/grammar.sql --step 1 > /dev/shm/qa/log/pstress_run.log[ ✓ ]
06:34:25  Cluster restart is successful                                                                       [ ✓ ]
06:34:57  Cluster restart is successful                                                                       [ ✓ ]
06:35:26  Cluster restart is successful                                                                       [ ✓ ]
06:35:26  PSTRESS RUN command : /home/vagrant/pstress/src/pstress-pxc --database=test --threads=10 --logdir=/dev/shm/qa/log --log-all-queries --log-failed-queries --user=root --socket=/dev/shm/qa/node1/mysql.sock --seed 1000 --tables 5 --records 10 --no-encryption --seconds 30 --grammar-file /home/vagrant/pstress/src/grammar.sql --step 2 > /dev/shm/qa/log/pstress_run.log[ ✓ ]
06:36:08  Cluster restart is successful                                                                       [ ✓ ]
06:36:38  Cluster restart is successful                                                                       [ ✓ ]
06:37:08  Cluster restart is successful                                                                       [ ✓ ]
06:37:08  PSTRESS RUN command : /home/vagrant/pstress/src/pstress-pxc --database=test --threads=10 --logdir=/dev/shm/qa/log --log-all-queries --log-failed-queries --user=root --socket=/dev/shm/qa/node1/mysql.sock --seed 1000 --tables 5 --records 10 --no-encryption --seconds 30 --grammar-file /home/vagrant/pstress/src/grammar.sql --step 3 > /dev/shm/qa/log/pstress_run.log[ ✓ ]
06:37:48  Cluster restart is successful                                                                       [ ✓ ]
06:38:18  Cluster restart is successful                                                                       [ ✓ ]
06:38:49  Cluster restart is successful                                                                       [ ✓ ]
06:38:49  PSTRESS RUN command : /home/vagrant/pstress/src/pstress-pxc --database=test --threads=10 --logdir=/dev/shm/qa/log --log-all-queries --log-failed-queries --user=root --socket=/dev/shm/qa/node1/mysql.sock --seed 1000 --tables 5 --records 10 --no-encryption --seconds 30 --grammar-file /home/vagrant/pstress/src/grammar.sql --step 4 > /dev/shm/qa/log/pstress_run.log[ ✓ ]
06:39:29  Cluster restart is successful                                                                       [ ✓ ]
06:39:59  Cluster restart is successful                                                                       [ ✓ ]
06:40:31  Cluster restart is successful                                                                       [ ✓ ]
06:40:31  PSTRESS RUN command : /home/vagrant/pstress/src/pstress-pxc --database=test --threads=10 --logdir=/dev/shm/qa/log --log-all-queries --log-failed-queries --user=root --socket=/dev/shm/qa/node1/mysql.sock --seed 1000 --tables 5 --records 10 --no-encryption --seconds 30 --grammar-file /home/vagrant/pstress/src/grammar.sql --step 5 > /dev/shm/qa/log/pstress_run.log[ ✓ ]
06:41:11  Cluster restart is successful                                                                       [ ✓ ]
06:41:40  Cluster restart is successful                                                                       [ ✓ ]
06:42:11  Cluster restart is successful                                                                       [ ✓ ]
06:42:11  PSTRESS RUN command : /home/vagrant/pstress/src/pstress-pxc --database=test --threads=10 --logdir=/dev/shm/qa/log --log-all-queries --log-failed-queries --user=root --socket=/dev/shm/qa/node1/mysql.sock --seed 1000 --tables 5 --records 10 --no-encryption --seconds 30 --grammar-file /home/vagrant/pstress/src/grammar.sql --step 6 > /dev/shm/qa/log/pstress_run.log[ ✓ ]


$ python3 qa_framework.py --tests=random_qa.pstress_random_qa.py

--------------------
PXC Random PSTRESS QA
--------------------
06:53:59  Startup sanity check                                                                                [ ✓ ]
06:53:59  Configuration file creation                                                                         [ ✓ ]
06:54:39  Initializing cluster                                                                                [ ✓ ]
06:55:38  Cluster startup                                                                                     [ ✓ ]
06:55:38  Database connection                                                                                 [ ✓ ]
06:55:38  PSTRESS RUN command : /home/vagrant/pstress/src/pstress-pxc --database=test --threads=16 --logdir=/dev/shm/qa/log --log-all-queries --log-failed-queries --user=root --socket=/dev/shm/qa/node1/mysql.sock --seed 100 --tables 16 --no-encryption --seconds 30  --grammar-file /home/vagrant/pstress/src/grammar.sql --records 100> /dev/shm/qa/log/pstress_run.log[ ✓ ]
06:56:27  Startup sanity check                                                                                [ ✓ ]
06:56:28  Configuration file creation                                                                         [ ✓ ]
06:57:10  Initializing cluster                                                                                [ ✓ ]
06:58:11  Cluster startup                                                                                     [ ✓ ]
06:58:11  Database connection                                                                                 [ ✓ ]
06:58:11  PSTRESS RUN command : /home/vagrant/pstress/src/pstress-pxc --database=test --threads=16 --logdir=/dev/shm/qa/log --log-all-queries --log-failed-queries --user=root --socket=/dev/shm/qa/node1/mysql.sock --seed 500 --tables 16 --no-encryption --seconds 30  --grammar-file /home/vagrant/pstress/src/grammar.sql --records 100> /dev/shm/qa/log/pstress_run.log[ ✓ ]
06:58:55  Startup sanity check                                                                                [ ✓ ]
06:58:55  Configuration file creation                                                                         [ ✓ ]
06:59:36  Initializing cluster                                                                                [ ✓ ]
07:00:37  Cluster startup                                                                                     [ ✓ ]
07:00:37  Database connection                                                                                 [ ✓ ]
07:00:37  PSTRESS RUN command : /home/vagrant/pstress/src/pstress-pxc --database=test --threads=16 --logdir=/dev/shm/qa/log --log-all-queries --log-failed-queries --user=root --socket=/dev/shm/qa/node1/mysql.sock --seed 1000 --tables 16 --no-encryption --seconds 30  --grammar-file /home/vagrant/pstress/src/grammar.sql --records 100> /dev/shm/qa/log/pstress_run.log[ ✓ ]
07:01:23  Startup sanity check                                                                                [ ✓ ]
07:01:23  Configuration file creation                                                                         [ ✓ ]
07:02:05  Initializing cluster                                                                                [ ✓ ]
07:03:04  Cluster startup                                                                                     [ ✓ ]
07:03:04  Database connection                                                                                 [ ✓ ]
07:03:04  PSTRESS RUN command : /home/vagrant/pstress/src/pstress-pxc --database=test --threads=16 --logdir=/dev/shm/qa/log --log-all-queries --log-failed-queries --user=root --socket=/dev/shm/qa/node1/mysql.sock --seed 100 --tables 16 --no-encryption --seconds 30  --grammar-file /home/vagrant/pstress/src/grammar.sql --records 500> /dev/shm/qa/log/pstress_run.log[ ✓ ]
[...]
```

