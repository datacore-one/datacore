# A stand-in for an agent's old host, to rehearse bin/agent-cutover end to end
# without touching a real one: sshd, sudo, crontab, a recording systemctl, and
# a home directory shaped like a Hermes agent's (including an owner key that
# must never be copied).
FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      openssh-server sudo cron procps python3 tar git ca-certificates \
    && rm -rf /var/lib/apt/lists/* && mkdir -p /run/sshd
RUN userdel -r ubuntu && useradd -m -d /var/lib/agent -u 1000 -s /bin/bash agent \
    && echo 'agent ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/agent
COPY systemctl /usr/local/bin/systemctl
COPY seed-home /usr/local/bin/seed-home
RUN chmod 755 /usr/local/bin/systemctl /usr/local/bin/seed-home \
    && touch /var/log/fake-systemctl.log && chmod 666 /var/log/fake-systemctl.log
COPY authorized_keys /var/lib/agent/.ssh/authorized_keys
RUN chown -R agent:agent /var/lib/agent/.ssh && chmod 700 /var/lib/agent/.ssh \
    && chmod 600 /var/lib/agent/.ssh/authorized_keys \
    && su agent -c /usr/local/bin/seed-home
EXPOSE 22
CMD ["/usr/sbin/sshd", "-D", "-e"]
