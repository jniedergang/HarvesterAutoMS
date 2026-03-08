# Stage 1: Retrieve VMDP drivers from SUSE container image
ARG VMDP_VERSION=2.5.4.3
FROM registry.suse.com/suse/vmdp/vmdp:${VMDP_VERSION} AS vmdp

# Stage 2: Extract drivers from the VMDP ISO
FROM registry.opensuse.org/opensuse/tumbleweed:latest AS builder
ARG VMDP_VERSION=2.5.4.3
RUN zypper -n install 7zip && zypper clean -a
COPY --from=vmdp /disk/VMDP-WIN-${VMDP_VERSION}.iso /tmp/vmdp.iso
RUN 7z x /tmp/vmdp.iso -o/tmp/vmdp-extracted \
    && mkdir -p /drivers \
    && cp -r /tmp/vmdp-extracted/win10-11-server22/x64/pvvx/* /drivers/ \
    && rm -rf /tmp/vmdp*

# Stage 3: Final image with Flask + genisoimage + pre-extracted drivers
FROM registry.opensuse.org/opensuse/tumbleweed:latest
RUN zypper -n install python312 python312-Flask mkisofs && zypper clean -a \
    && ln -s /usr/bin/python3.12 /usr/bin/python3

# Builtin drivers (fixed, in image — never overwritten by volumes)
COPY --from=builder /drivers /app/vmdp-drivers-builtin
# Default drivers dir (can be overridden by volume mount)
COPY --from=builder /drivers /app/drivers/vmdp

COPY app.py index.html build-iso-from-xml.sh /app/
RUN chmod +x /app/build-iso-from-xml.sh

WORKDIR /app
EXPOSE 8098

ENV CONFIGS_DIR=/app/configs
ENV OUTPUT_DIR=/app/iso
ENV XML_DIR=/app/xml
ENV DRIVERS_DIR=/app/drivers
ENV IMAGES_DIR=/app/images

CMD ["python3", "app.py"]
