package dev.donutsmp.rtpmapper.data;

import dev.donutsmp.rtpmapper.config.ConfigManager;
import dev.donutsmp.rtpmapper.DonutRtpMapperMod;
import java.io.BufferedReader;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.stream.Collectors;

public final class SampleStore {
	private static final String CSV_HEADER = RtpSampleCsv.HEADER;
	private static final String LEGACY_CSV_HEADER = "timestamp,session_id,x,y,z,dimension,distance_from_origin";
	private static final int MAX_LOADED_SAMPLES = 100_000;
	private static final SampleStore INSTANCE = new SampleStore();

	private final List<RtpSample> allSamples = new ArrayList<>();
	private final List<RtpSample> sessionSamples = new ArrayList<>();
	private String currentSessionId = "";

	private SampleStore() {
	}

	public static SampleStore get() {
		return INSTANCE;
	}

	public void startSession() {
		currentSessionId = DonutRtpMapperMod.MOD_ID + "-" + System.currentTimeMillis();
		sessionSamples.clear();
	}

	public String getCurrentSessionId() {
		return currentSessionId;
	}

	public void addSample(RtpSample sample) {
		allSamples.add(sample);
		sessionSamples.add(sample);
	}

	public List<RtpSample> getAllSamples() {
		return Collections.unmodifiableList(allSamples);
	}

	public List<RtpSample> getSessionSamples() {
		return Collections.unmodifiableList(sessionSamples);
	}

	public void clearSession() {
		sessionSamples.clear();
	}

	public void clearAll() {
		allSamples.clear();
		sessionSamples.clear();
	}

	public void loadExisting() {
		allSamples.clear();
		sessionSamples.clear();
		Path csvPath = getSamplesCsvPath();
		if (!Files.exists(csvPath)) {
			return;
		}

		try (BufferedReader reader = Files.newBufferedReader(csvPath, StandardCharsets.UTF_8)) {
			String line = reader.readLine();
			if (line != null && !CSV_HEADER.equals(line) && !LEGACY_CSV_HEADER.equals(line)) {
				RtpSampleCsv.decode(line).ifPresent(allSamples::add);
			}
			while (allSamples.size() < MAX_LOADED_SAMPLES && (line = reader.readLine()) != null) {
				RtpSampleCsv.decode(line).ifPresent(allSamples::add);
			}
		} catch (IOException ignored) {
		}
	}

	public void appendSample(RtpSample sample) throws IOException {
		Path configDir = ConfigManager.get().getConfigDir();
		Files.createDirectories(configDir);
		Files.createDirectories(configDir.resolve("sessions"));

		Path csvPath = getSamplesCsvPath();
		Path txtPath = configDir.resolve("samples.txt");
		Path sessionCsv = configDir.resolve("sessions").resolve(currentSessionId + ".csv");

		migrateLegacyCsvIfNeeded(csvPath);
		writeHeaderIfNeeded(csvPath);
		writeHeaderIfNeeded(sessionCsv);

		String row = RtpSampleCsv.encode(sample);
		Files.writeString(csvPath, row + System.lineSeparator(), StandardCharsets.UTF_8,
				StandardOpenOption.CREATE, StandardOpenOption.APPEND);
		Files.writeString(sessionCsv, row + System.lineSeparator(), StandardCharsets.UTF_8,
				StandardOpenOption.CREATE, StandardOpenOption.APPEND);
		Files.writeString(txtPath, sample.toTextLine() + System.lineSeparator(), StandardCharsets.UTF_8,
				StandardOpenOption.CREATE, StandardOpenOption.APPEND);
	}

	public void exportViewCsv(List<RtpSample> samples, Path target) throws IOException {
		Files.createDirectories(target.getParent());
		String content = CSV_HEADER + System.lineSeparator()
				+ samples.stream().map(RtpSampleCsv::encode).collect(Collectors.joining(System.lineSeparator()));
		if (!samples.isEmpty()) {
			content += System.lineSeparator();
		}
		Files.writeString(target, content, StandardCharsets.UTF_8);
	}

	public Path getSamplesCsvPath() {
		return ConfigManager.get().getConfigDir().resolve("samples.csv");
	}

	private void writeHeaderIfNeeded(Path path) throws IOException {
		if (!Files.exists(path) || Files.size(path) == 0) {
			Files.writeString(path, CSV_HEADER + System.lineSeparator(), StandardCharsets.UTF_8,
					StandardOpenOption.CREATE, StandardOpenOption.WRITE);
		}
	}

	private void migrateLegacyCsvIfNeeded(Path path) throws IOException {
		if (!Files.exists(path)) {
			return;
		}

		List<String> lines = Files.readAllLines(path, StandardCharsets.UTF_8);
		if (lines.isEmpty() || !LEGACY_CSV_HEADER.equals(lines.getFirst())) {
			return;
		}

		List<String> migrated = new ArrayList<>();
		migrated.add(CSV_HEADER);
		for (int index = 1; index < lines.size(); index++) {
			String[] parts = lines.get(index).split(",", -1);
			if (parts.length == 7) {
				parts[5] = "unknown";
				migrated.add(String.join(",", parts));
			}
		}

		Path temporary = path.resolveSibling(path.getFileName() + ".migrating");
		Files.write(temporary, migrated, StandardCharsets.UTF_8,
				StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING,
				StandardOpenOption.WRITE);
		try {
			Files.move(temporary, path, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
		} catch (java.nio.file.AtomicMoveNotSupportedException exception) {
			Files.move(temporary, path, StandardCopyOption.REPLACE_EXISTING);
		}
	}
}
