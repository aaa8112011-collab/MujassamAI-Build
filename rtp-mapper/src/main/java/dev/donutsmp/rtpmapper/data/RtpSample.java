package dev.donutsmp.rtpmapper.data;

import dev.donutsmp.rtpmapper.engine.RtpRegion;

import java.time.Instant;
import java.util.Locale;
import java.util.UUID;

public record RtpSample(
		String id,
		String sessionId,
		Instant timestamp,
		double x,
		double y,
		double z,
		String requestedRegion
) {
	public static RtpSample create(String sessionId, double x, double y, double z, RtpRegion requestedRegion) {
		return new RtpSample(
				UUID.randomUUID().toString(),
				sessionId,
				Instant.now(),
				x,
				y,
				z,
				requestedRegion.id()
		);
	}

	public double distanceFromOrigin() {
		return Math.sqrt(x * x + z * z);
	}

	public String requestedRegionDisplayName() {
		RtpRegion region = RtpRegion.fromIdOrNull(requestedRegion);
		return region == null ? "Unknown" : region.displayName();
	}

	public String toCsvRow() {
		return RtpSampleCsv.encode(this);
	}

	public String toTextLine() {
		return String.format(Locale.ROOT,
				"[%s] session=%s requestedRegion=%s x=%.1f y=%.1f z=%.1f dist=%.1f",
				timestamp,
				sessionId,
				requestedRegion,
				x,
				y,
				z,
				distanceFromOrigin()
		);
	}
}
