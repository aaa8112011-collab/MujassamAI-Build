package dev.donutsmp.rtpmapper.config;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import dev.donutsmp.rtpmapper.DonutRtpMapperMod;
import net.fabricmc.loader.api.FabricLoader;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;

public final class ConfigManager {
	private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();
	private static final ConfigManager INSTANCE = new ConfigManager();

	private final Path configDir;
	private final Path configFile;
	private MapperConfig config = new MapperConfig();

	private ConfigManager() {
		this.configDir = FabricLoader.getInstance().getConfigDir().resolve(DonutRtpMapperMod.CONFIG_FOLDER);
		this.configFile = configDir.resolve("config.json");
	}

	public static ConfigManager get() {
		return INSTANCE;
	}

	public Path getConfigDir() {
		return configDir;
	}

	public MapperConfig getConfig() {
		return config;
	}

	public void load() {
		try {
			Files.createDirectories(configDir);
			if (Files.exists(configFile)) {
				String json = Files.readString(configFile, StandardCharsets.UTF_8);
				MapperConfig loaded = GSON.fromJson(json, MapperConfig.class);
				if (loaded != null) {
					loaded.normalize();
					config = loaded;
				}
			} else {
				config.normalize();
				save();
			}
		} catch (RuntimeException exception) {
			config = new MapperConfig();
			config.normalize();
			preserveInvalidConfig();
			DonutRtpMapperMod.LOGGER.warn("Could not load RTP Mapper config; defaults were restored", exception);
		} catch (IOException exception) {
			config = new MapperConfig();
			config.normalize();
			DonutRtpMapperMod.LOGGER.warn("Could not read RTP Mapper config; defaults are active", exception);
		}
	}

	public void save() {
		Path temporary = configFile.resolveSibling(configFile.getFileName() + ".tmp");
		try {
			Files.createDirectories(configDir);
			config.normalize();
			Files.writeString(temporary, GSON.toJson(config), StandardCharsets.UTF_8,
					StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING,
					StandardOpenOption.WRITE);
			try {
				Files.move(temporary, configFile, StandardCopyOption.REPLACE_EXISTING,
						StandardCopyOption.ATOMIC_MOVE);
			} catch (java.nio.file.AtomicMoveNotSupportedException exception) {
				Files.move(temporary, configFile, StandardCopyOption.REPLACE_EXISTING);
			}
		} catch (IOException exception) {
			DonutRtpMapperMod.LOGGER.warn("Could not save RTP Mapper config", exception);
			try {
				Files.deleteIfExists(temporary);
			} catch (IOException ignored) {
			}
		}
	}

	public void update(MapperConfig updated) {
		config = updated.copy();
		config.normalize();
		save();
	}

	private void preserveInvalidConfig() {
		if (!Files.exists(configFile)) {
			return;
		}
		Path backup = configFile.resolveSibling("config.invalid-" + System.currentTimeMillis() + ".json");
		try {
			Files.move(configFile, backup, StandardCopyOption.REPLACE_EXISTING);
		} catch (IOException exception) {
			DonutRtpMapperMod.LOGGER.warn("Could not preserve invalid RTP Mapper config", exception);
		}
	}
}
