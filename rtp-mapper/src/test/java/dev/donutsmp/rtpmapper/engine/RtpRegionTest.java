package dev.donutsmp.rtpmapper.engine;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;

import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

final class RtpRegionTest {
	@Test
	void canonicalIdsAndCommandArgumentsAreStableAndUnique() {
		List<String> expected = List.of("east", "west", "eu central", "eu west", "asia", "oceania");
		assertEquals(expected, RtpRegion.canonicalIds());
		assertEquals(expected, Arrays.stream(RtpRegion.values()).map(RtpRegion::commandArgument).toList());
		assertEquals(expected.size(), new HashSet<>(expected).size());
		for (RtpRegion region : RtpRegion.values()) {
			assertSame(region, RtpRegion.fromIdOrNull(region.id()));
		}
	}

	static Stream<Arguments> aliases() {
		return Stream.of(
				Arguments.of("NA-EAST", RtpRegion.EAST),
				Arguments.of("north_america_west", RtpRegion.WEST),
				Arguments.of("EU__CENTRAL", RtpRegion.EU_CENTRAL),
				Arguments.of("Europe-West", RtpRegion.EU_WEST),
				Arguments.of(" oceanic ", RtpRegion.OCEANIA));
	}

	@ParameterizedTest
	@MethodSource("aliases")
	void normalizesDocumentedConfigAliases(String input, RtpRegion expected) {
		assertSame(expected, RtpRegion.fromIdOrNull(input));
	}

	@ParameterizedTest
	@NullAndEmptySource
	@ValueSource(strings = {"mars", "north", "eu", "east coast"})
	void unknownRegionNeverSilentlyMapsToEast(String input) {
		assertNull(RtpRegion.fromIdOrNull(input));
	}
}
