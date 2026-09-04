package dev.donutsmp.rtpmapper.engine;

import java.util.Arrays;
import java.util.List;
import java.util.Locale;

public enum RtpRegion {
	EAST("east", "NA East", "east"),
	WEST("west", "NA West", "west"),
	EU_CENTRAL("eu central", "EU Central", "eu central"),
	EU_WEST("eu west", "EU West", "eu west"),
	ASIA("asia", "Asia", "asia"),
	OCEANIA("oceania", "Oceania", "oceania");

	private final String id;
	private final String displayName;
	private final String commandArgument;

	RtpRegion(String id, String displayName, String commandArgument) {
		this.id = id;
		this.displayName = displayName;
		this.commandArgument = commandArgument;
	}

	public String id() {
		return id;
	}

	public String displayName() {
		return displayName;
	}

	public String commandArgument() {
		return commandArgument;
	}

	public static List<String> canonicalIds() {
		return Arrays.stream(values()).map(RtpRegion::id).toList();
	}

	public static RtpRegion fromId(String value) {
		RtpRegion region = fromIdOrNull(value);
		return region == null ? EAST : region;
	}

	public static RtpRegion fromIdOrNull(String value) {
		if (value == null) {
			return null;
		}

		String normalized = normalize(value);

		return switch (normalized) {
			case "east", "na east", "north america east" -> EAST;
			case "west", "na west", "north america west" -> WEST;
			case "eu central", "europe central" -> EU_CENTRAL;
			case "eu west", "europe west" -> EU_WEST;
			case "asia" -> ASIA;
			case "oceania", "oceanic" -> OCEANIA;
			default -> null;
		};
	}

	public static RtpRegion fromCommandArgument(String value) {
		if (value == null) {
			return null;
		}
		String normalized = value.trim().toLowerCase(Locale.ROOT).replaceAll("\\s+", " ");
		for (RtpRegion region : values()) {
			if (region.commandArgument.equals(normalized)) {
				return region;
			}
		}
		return null;
	}

	private static String normalize(String value) {
		return value.trim()
				.toLowerCase(Locale.ROOT)
				.replace('-', ' ')
				.replace('_', ' ')
				.replaceAll("\\s+", " ");
	}
}
