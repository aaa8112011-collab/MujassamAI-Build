package dev.donutsmp.rtpmapper.data;

import dev.donutsmp.rtpmapper.engine.RtpRegion;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Locale;
import java.util.Optional;
import java.util.UUID;
import java.util.regex.Pattern;

/** Strict seven-column codec. Values are constrained so quoting is never required. */
public final class RtpSampleCsv {
	public static final String HEADER =
			"timestamp,session_id,x,y,z,requested_region,distance_from_origin";
	private static final Pattern SAFE_SESSION_ID =
			Pattern.compile("[A-Za-z0-9][A-Za-z0-9._-]{0,127}");

	private RtpSampleCsv() {
	}

	public static String encode(RtpSample sample) {
		validate(sample);
		RtpRegion parsedRegion = RtpRegion.fromIdOrNull(sample.requestedRegion());
		String canonicalRegion = parsedRegion == null ? "unknown" : parsedRegion.id();
		return String.format(Locale.ROOT,
				"%s,%s,%.3f,%.3f,%.3f,%s,%.3f",
				sample.timestamp(), sample.sessionId(), sample.x(), sample.y(), sample.z(),
				canonicalRegion, sample.distanceFromOrigin());
	}

	public static Optional<RtpSample> decode(String row) {
		if (row == null || row.indexOf('\n') >= 0 || row.indexOf('\r') >= 0) {
			return Optional.empty();
		}
		String[] parts = row.split(",", -1);
		if (parts.length != 7 || !SAFE_SESSION_ID.matcher(parts[1]).matches()) {
			return Optional.empty();
		}

		try {
			Instant timestamp = Instant.parse(parts[0]);
			double x = Double.parseDouble(parts[2]);
			double y = Double.parseDouble(parts[3]);
			double z = Double.parseDouble(parts[4]);
			double storedDistance = Double.parseDouble(parts[6]);
			if (!Double.isFinite(x) || !Double.isFinite(y) || !Double.isFinite(z)
					|| !Double.isFinite(storedDistance)) {
				return Optional.empty();
			}

			String region = parts[5];
			RtpRegion parsed = RtpRegion.fromIdOrNull(region);
			if (parsed != null) {
				region = parsed.id();
			} else if (!"unknown".equals(region)) {
				return Optional.empty();
			}

			String id = "loaded-" + UUID.nameUUIDFromBytes(row.getBytes(StandardCharsets.UTF_8));
			return Optional.of(new RtpSample(id, parts[1], timestamp, x, y, z, region));
		} catch (RuntimeException exception) {
			return Optional.empty();
		}
	}

	private static void validate(RtpSample sample) {
		if (sample == null || sample.timestamp() == null
				|| !SAFE_SESSION_ID.matcher(sample.sessionId()).matches()) {
			throw new IllegalArgumentException("Invalid RTP sample identity");
		}
		if (!Double.isFinite(sample.x()) || !Double.isFinite(sample.y()) || !Double.isFinite(sample.z())) {
			throw new IllegalArgumentException("RTP coordinates must be finite");
		}
		RtpRegion parsed = RtpRegion.fromIdOrNull(sample.requestedRegion());
		if (parsed == null && !"unknown".equals(sample.requestedRegion())) {
			throw new IllegalArgumentException("Unknown requested region");
		}
	}
}
