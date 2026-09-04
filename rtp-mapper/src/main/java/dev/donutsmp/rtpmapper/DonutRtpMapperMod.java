package dev.donutsmp.rtpmapper;

import net.minecraft.resources.Identifier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public final class DonutRtpMapperMod {
	public static final String MOD_ID = "donut-smp-rtp-mapper";
	public static final String CONFIG_FOLDER = "donut-smp-rtp-mapper";
	public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

	private DonutRtpMapperMod() {
	}

	public static Identifier id(String path) {
		return Identifier.fromNamespaceAndPath(MOD_ID, path);
	}
}
