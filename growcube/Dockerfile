ARG BUILD_FROM
FROM ${BUILD_FROM}

COPY run.sh /run.sh
COPY app /app

RUN chmod a+x /run.sh

WORKDIR /app
CMD ["/run.sh"]

