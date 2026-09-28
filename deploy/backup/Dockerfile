# Same major as the db service so pg_dump/pg_restore match the server.
FROM postgres:16-alpine
RUN apk add --no-cache rsync
COPY --chmod=0755 jyj-backup.sh /usr/local/bin/jyj-backup
ENTRYPOINT ["/usr/local/bin/jyj-backup"]
CMD ["schedule"]
