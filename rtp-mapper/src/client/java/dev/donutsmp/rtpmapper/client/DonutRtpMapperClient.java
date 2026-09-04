package dev.donutsmp.rtpmapper.client;

import com.mojang.blaze3d.platform.InputConstants;
import dev.donutsmp.rtpmapper.DonutRtpMapperMod;
import dev.donutsmp.rtpmapper.client.ui.MapperScreen;
import dev.donutsmp.rtpmapper.config.ConfigManager;
import dev.donutsmp.rtpmapper.data.SampleStore;
import dev.donutsmp.rtpmapper.engine.RtpMapperEngine;
import dev.donutsmp.rtpmapper.render.MapperHudOverlay;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.keybinding.v1.KeyBindingHelper;
import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
import net.fabricmc.fabric.api.client.message.v1.ClientSendMessageEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.fabric.api.client.rendering.v1.hud.HudElementRegistry;
import net.fabricmc.fabric.api.client.rendering.v1.hud.VanillaHudElements;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.Screen;
import org.lwjgl.glfw.GLFW;

public final class DonutRtpMapperClient implements ClientModInitializer {
	private static final KeyMapping.Category CATEGORY =
			KeyMapping.Category.register(DonutRtpMapperMod.id("category"));

	private static KeyMapping openMapperKey;
	private static KeyMapping toggleHudKey;
	private static KeyMapping toggleMappingKey;

	@Override
	public void onInitializeClient() {
		ConfigManager.get().load();
		SampleStore.get().loadExisting();

		openMapperKey = KeyBindingHelper.registerKeyBinding(new KeyMapping(
				"key.donut-smp-rtp-mapper.open_mapper",
				InputConstants.Type.KEYSYM,
				GLFW.GLFW_KEY_M,
				CATEGORY
		));

		toggleHudKey = KeyBindingHelper.registerKeyBinding(new KeyMapping(
				"key.donut-smp-rtp-mapper.toggle_hud",
				InputConstants.Type.KEYSYM,
				GLFW.GLFW_KEY_N,
				CATEGORY
		));

		toggleMappingKey = KeyBindingHelper.registerKeyBinding(new KeyMapping(
				"key.donut-smp-rtp-mapper.toggle_mapping",
				InputConstants.Type.KEYSYM,
				GLFW.GLFW_KEY_K,
				CATEGORY
		));

		ClientTickEvents.END_CLIENT_TICK.register(client -> {
			handleKeybinds(client);
			RtpMapperEngine.get().tick();
		});

		ClientReceiveMessageEvents.GAME.register((message, overlay) -> {
			String text = message.getString();
			if (overlay) {
				RtpMapperEngine.get().onActionBarMessage(text);
			} else {
				RtpMapperEngine.get().onChatMessage(text);
			}
		});

		ClientSendMessageEvents.COMMAND.register(command ->
				RtpMapperEngine.get().onUserCommand(command)
		);

		ClientPlayConnectionEvents.DISCONNECT.register((handler, client) ->
				RtpMapperEngine.get().onDisconnect()
		);

		HudElementRegistry.attachElementBefore(
				VanillaHudElements.CHAT,
				DonutRtpMapperMod.id("mapper_hud"),
				(graphics, tickCounter) -> {
					if (ConfigManager.get().getConfig().hudEnabled) {
						MapperHudOverlay.render(graphics);
					}
				}
		);
	}

	private static void handleKeybinds(Minecraft client) {
		while (openMapperKey.consumeClick()) {
			Screen current = client.screen;
			if (current instanceof MapperScreen) {
				client.setScreen(null);
			} else {
				client.setScreen(new MapperScreen());
			}
		}

		while (toggleHudKey.consumeClick()) {
			var config = ConfigManager.get().getConfig();
			config.hudEnabled = !config.hudEnabled;
			ConfigManager.get().update(config);
		}

		while (toggleMappingKey.consumeClick()) {
			RtpMapperEngine.get().toggleRunning();
		}
	}
}
