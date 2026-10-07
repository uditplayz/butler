FROM debian:bookworm-slim AS build
ARG SRTLA_REF=7fa6985dedd5028b8939e2ac3b32136ca7ad8d4c
RUN apt-get update && apt-get install -y --no-install-recommends git cmake g++ make ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/irlserver/srtla.git /src \
    && git -C /src checkout -q "$SRTLA_REF" && git -C /src submodule update --init -q \
    && cmake -S /src -B /src/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /src/build --target srtla_rec -j"$(nproc)"

FROM debian:bookworm-slim
COPY --from=build /src/build/srtla_rec /usr/local/bin/srtla_rec
RUN useradd --system --no-create-home --uid 10002 srtla
USER srtla
EXPOSE 5000/udp
ENTRYPOINT ["srtla_rec"]
CMD ["--srtla_port", "5000", "--srt_hostname", "127.0.0.1", "--srt_port", "8890"]
