package dev.donutsmp.rtpmapper.data;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Instant;
import java.util.Locale;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;

final class RtpSampleCsvTest {
	private static RtpSample sample(String sessionId, String region) {
		return new RtpSample("sample-1", sessionId, Instant.parse("2026-09-04T12:00:00Z"),
				3.0, 64.25, -4.0, region);
	}

	@Test
	void writesLocaleIndependentNumbersAndCorrectRadius() {
		Locale old = Locale.getDefault(Locale.Category.FORMAT);
		try {
			Locale.setDefault(Locale.Category.FORMAT, Locale.GERMANY);
			RtpSample sample = sample("session-1", "eu central");
			assertEquals(5.0, sample.distanceFromOrigin(), 1.0e-12);
			assertEquals("2026-09-04T12:00:00Z,session-1,3.000,64.250,-4.000,eu central,5.000",
					RtpSampleCsv.encode(sample));
		} finally {
			Locale.setDefault(Locale.Category.FORMAT, old);
		}
	}

	@Test
	void safeRowRoundTripsPersistedFields() {
		RtpSample original = new RtpSample("sample-1", "session-1",
				Instant.parse("2026-09-04T12:00:00Z"), 3.1234, 64.5678, -4.9876, "west");
		RtpSample loaded = RtpSampleCsv.decode(RtpSampleCsv.encode(original)).orElseThrow();
		assertEquals(original.timestamp(), loaded.timestamp());
		assertEquals(original.sessionId(), loaded.sessionId());
		assertEquals(original.x(), loaded.x(), 0.00051);
		assertEquals(original.y(), loaded.y(), 0.00051);
		assertEquals(original.z(), loaded.z(), 0.00051);
		assertEquals(original.requestedRegion(), loaded.requestedRegion());
	}

	@ParameterizedTest
	@ValueSource(strings = {"=2+2", "+cmd", "-cmd", "@cmd", "bad,id", "bad\nid", "\tcmd"})
	void refusesSpreadsheetOrRowInjectionInSessionId(String sessionId) {
		assertThrows(IllegalArgumentException.class, () -> RtpSampleCsv.encode(sample(sessionId, "east")));
	}

	static Stream<String> malformedRows() {
		return Stream.of(
				"not-an-instant,session-1,3,64,-4,east,5",
				"2026-09-04T12:00:00Z,session-1,NaN,64,-4,east,5",
				"2026-09-04T12:00:00Z,session-1,3,64,-4,east,Infinity",
				"2026-09-04T12:00:00Z,=2+2,3,64,-4,east,5",
				"2026-09-04T12:00:00Z,session-1,3,64,-4,mars,5",
				"2026-09-04T12:00:00Z,session-1,3,64,-4,east,5,extra");
	}

	@ParameterizedTest
	@MethodSource("malformedRows")
	void rejectsMalformedOrUnsafeRows(String row) {
		assertTrue(RtpSampleCsv.decode(row).isEmpty());
	}
}
