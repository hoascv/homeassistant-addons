# Hive standalone metastore 4.1.0 is the newest release Spark 4.1 will talk to
# (its supported set is 2.0.0–2.3.10, 3.0.0–3.1.3, 4.0.0–4.1.0), it is compiled
# for Java 17, and its pom builds against Hadoop 3.4.1 — hence all three pins.
FROM docker.io/library/eclipse-temurin:17-jre-noble

ENV HADOOP_VERSION=3.4.1 \
    METASTORE_VERSION=4.1.0 \
    HADOOP_HOME=/opt/hadoop \
    METASTORE_HOME=/opt/hive-metastore \
    PATH="/opt/hive-metastore/bin:/opt/hadoop/bin:${PATH}"

# jq drives the entrypoint; psql provisions the metastore role/database on the
# Postgres add-on (its own init hooks only ever run on a first-time database).
# The metastore tarball ships no Hadoop and no JDBC driver: bin/ext/metastore.sh
# launches through `$HADOOP_HOME/bin/hadoop jar`, so a real Hadoop distribution
# is a hard requirement rather than a convenience.
# hadolint ignore=DL3008,DL3015
RUN apt-get update \
    && apt-get install -y --no-install-recommends jq curl postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://archive.apache.org/dist/hadoop/common/hadoop-${HADOOP_VERSION}/hadoop-${HADOOP_VERSION}.tar.gz" \
         -o /tmp/hadoop.tgz \
    && mkdir -p "${HADOOP_HOME}" \
    && tar -xzf /tmp/hadoop.tgz -C "${HADOOP_HOME}" --strip-components=1 \
    && rm /tmp/hadoop.tgz \
    && curl -fsSL "https://archive.apache.org/dist/hive/hive-standalone-metastore-${METASTORE_VERSION}/hive-standalone-metastore-${METASTORE_VERSION}-bin.tar.gz" \
         -o /tmp/metastore.tgz \
    && mkdir -p "${METASTORE_HOME}" \
    && tar -xzf /tmp/metastore.tgz -C "${METASTORE_HOME}" --strip-components=1 \
    && rm /tmp/metastore.tgz \
    && curl -fsSL https://jdbc.postgresql.org/download/postgresql-42.7.4.jar \
         -o "${METASTORE_HOME}/lib/postgresql-42.7.4.jar" \
    # The metastore resolves s3a:// warehouse and table locations itself (it
    # mkdirs a table directory at create time), so hadoop-aws and the AWS SDK
    # have to be on the *metastore* classpath. `hadoop classpath` omits
    # share/hadoop/tools/lib, so move them where it does look.
    && mv "${HADOOP_HOME}"/share/hadoop/tools/lib/hadoop-aws-*.jar \
          "${HADOOP_HOME}"/share/hadoop/tools/lib/bundle-*.jar \
          "${HADOOP_HOME}/share/hadoop/common/lib/" \
    # YARN and MapReduce look like dead weight — nothing here submits a job —
    # but they stay. Two things bite if they go: bin/hadoop exits 1 ("Invalid
    # HADOOP_YARN_HOME") unless those directories exist, and the metastore's
    # leader-election thread loads org.apache.hadoop.mapred.JobConf from
    # hadoop-mapreduce-client-core, so emptying them starts a server whose
    # housekeeping thread dies on ClassNotFoundException at every boot.
    # Only the tools directory (already raided for hadoop-aws above) and the
    # docs go.
    && rm -rf "${HADOOP_HOME}/share/hadoop/tools" \
              "${HADOOP_HOME}/share/doc" \
    && rm -rf /var/tmp/* /tmp/*

COPY run.sh /run.sh
RUN chmod +x /run.sh

ENTRYPOINT [ "/run.sh" ]
