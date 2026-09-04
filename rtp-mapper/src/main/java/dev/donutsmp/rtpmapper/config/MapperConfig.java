package dev.donutsmp.rtpmapper.config;

import dev.donutsmp.rtpmapper.engine.RtpRegion;

import java.util.ArrayList;
import java.util.List;

public final class MapperConfig {
	public List<String> enabledRegions = new ArrayList<>(RtpRegion.canonicalIds());

	public int teleportConfirmBlocks = 50;
	public int teleportConfirmTimeoutSeconds = 30;
	public int landingStabilizeTicks = 10;
	public String serverHost = "donutsmp.net";
	public boolean autoSaveAfterSample = true;
	public boolean hudEnabled = true;
	public String hudCorner = "top_left";
	public boolean hudShowMiniMap = true;

	public void normalize() {
		teleportConfirmBlocks = Math.max(1, teleportConfirmBlocks);
		teleportConfirmTimeoutSeconds = Math.max(5, teleportConfirmTimeoutSeconds);
		landingStabilizeTicks = Math.max(1, Math.min(40, landingStabilizeTicks));

		if (serverHost == null || serverHost.isBlank()) {
			serverHost = "donutsmp.net";
		}

		List<String> normalizedRegions = new ArrayList<>();
		if (enabledRegions != null) {
			for (String value : enabledRegions) {
				RtpRegion region = RtpRegion.fromIdOrNull(value);
				if (region != null && !normalizedRegions.contains(region.id())) {
					normalizedRegions.add(region.id());
				}
			}
		}
		if (normalizedRegions.isEmpty()) {
			normalizedRegions.addAll(RtpRegion.canonicalIds());
		}
		enabledRegions = normalizedRegions;

		if (hudCorner == null || !List.of("top_left", "top_right", "bottom_left", "bottom_right").contains(hudCorner)) {
			hudCorner = "top_left";
		}
	}

	public MapperConfig copy() {
		MapperConfig copy = new MapperConfig();
		copy.enabledRegions = new ArrayList<>(enabledRegions);
		copy.teleportConfirmBlocks = teleportConfirmBlocks;
		copy.teleportConfirmTimeoutSeconds = teleportConfirmTimeoutSeconds;
		copy.landingStabilizeTicks = landingStabilizeTicks;
		copy.serverHost = serverHost;
		copy.autoSaveAfterSample = autoSaveAfterSample;
		copy.hudEnabled = hudEnabled;
		copy.hudCorner = hudCorner;
		copy.hudShowMiniMap = hudShowMiniMap;
		copy.normalize();
		return copy;
	}
}
