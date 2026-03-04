import argparse
import json
import os
import cv2
import numpy as np
import librosa
from moviepy.editor import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, concatenate_videoclips, vfx
import moviepy.video.fx.all as vfx_all

def analyze_beats(audio_path):
    """Detecta los principales golpes de ritmo en el audio."""
    y, sr = librosa.load(audio_path)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    return beat_times

def apply_color_correction(clip):
    """Aplica un LUT simple o ajuste de contraste/brillo para 'Pop' visual."""
    def color_filter(image):
        # Aumentar un poco el contraste y la saturación
        img = cv2.cvtColor(image, cv2.COLOR_RGB2HSV).astype("float32")
        (h, s, v) = cv2.split(img)
        s = s * 1.2
        s = np.clip(s, 0, 255)
        v = v * 1.1
        v = np.clip(v, 0, 255)
        img = cv2.merge([h, s, v])
        img = cv2.cvtColor(img.astype("uint8"), cv2.COLOR_HSV2RGB)
        return img
    
    return clip.fl_image(color_filter)

def apply_punch_in(clip, start_t, end_t, zoom_factor=1.2):
    """Hace un zoom in digital en el fragmento especificado."""
    def effect(get_frame, t):
        img = get_frame(t)
        # Recorte de la imagen desde el centro
        h, w = img.shape[:2]
        new_h, new_w = int(h / zoom_factor), int(w / zoom_factor)
        top = (h - new_h) // 2
        bottom = top + new_h
        left = (w - new_w) // 2
        right = left + new_w
        cropped = img[top:bottom, left:right]
        return cv2.resize(cropped, (w, h))

    return clip.fl(effect)

def create_dynamic_subtitles(whisper_json_path, clip_size):
    """Parsea el JSON de Whisper y genera clips de texto estilo TikTok."""
    text_clips = []
    
    if not os.path.exists(whisper_json_path):
        return text_clips

    with open(whisper_json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    if "words" not in data:
        return text_clips

    for word_info in data["words"]:
        # Whisper retorna dicts con word, start y end
        word = word_info.get("word", "")
        start = word_info.get("start", 0)
        end = word_info.get("end", 0)
        duration = end - start
        
        # Saltamos palabras sin duración válida
        if duration <= 0:
            continue
            
        # Crear TextClip con MoviePy (requiere ImageMagick instalado)
        try:
            txt_clip = TextClip(word, fontsize=70, color='white', font='Arial-Bold', 
                              stroke_color='black', stroke_width=3, method='caption', 
                              size=(clip_size[0] * 0.8, None))
            
            # Posicionarlo en el centro, un poco hacia abajo
            txt_clip = txt_clip.set_position(('center', clip_size[1] * 0.7))
            txt_clip = txt_clip.set_start(start).set_duration(duration)
            
            text_clips.append(txt_clip)
        except Exception as e:
            print(f"Error generando texto para '{word}': {str(e)}")
            
    return text_clips

def process_video(metadata_path):
    print(f"Iniciando procesamiento de video usando metadata: {metadata_path}")
    
    with open(metadata_path, 'r', encoding='utf-8') as f:
        meta = json.load(f)
        
    input_video = meta.get("input_video")
    broll_video = meta.get("broll_video")
    whisper_json = meta.get("whisper_json")
    output_path = meta.get("output_video", "salida_ia.mp4")
    hooks = meta.get("hooks", []) # Lista de diccionarios {start: x, end: y, type: 'zoom'|'broll'}
    
    # 1. Cargar el original
    main_clip = VideoFileClip(input_video)
    audio_clip = main_clip.audio
    
    # 2. IA LUTs (Corrección de Color global)
    if meta.get("apply_color_correction", False):
        print("Aplicando corrección de color inteligente...")
        main_clip = apply_color_correction(main_clip)
        
    clips_to_composite = [main_clip]
    
    # 3. Ritmo y B-Roll
    if broll_video and os.path.exists(broll_video):
        print("Sincronizando B-Roll con ritmos...")
        # Exportamos temporalmente el audio para librosa si no se entregó un path separado
        audio_temp_path = "temp_audio_extract.wav"
        if not os.path.exists(audio_temp_path):
            audio_clip.write_audiofile(audio_temp_path, logger=None)
            
        beats = analyze_beats(audio_temp_path)
        
        # Encontramos el hook designado para B-roll
        broll_hook = next((h for h in hooks if h.get("type", "") == "broll"), None)
        if broll_hook:
            broll_start_requested = broll_hook["start"]
            broll_end = broll_hook["end"]
            
            # Ajustar broll_start al "beat" más cercano
            closest_beat = min(beats, key=lambda b: abs(b - broll_start_requested))
            print(f"B-Roll solicitado en {broll_start_requested}s, sincronizado al beat en {closest_beat}s")
            
            b_clip = VideoFileClip(broll_video).without_audio()
            # Cortar si es más largo que la duración del hook
            hook_dur = broll_end - closest_beat
            if b_clip.duration > hook_dur:
                b_clip = b_clip.subclip(0, hook_dur)
                
            # Resize and position
            b_clip = b_clip.resize(main_clip.size).set_position("center").set_start(closest_beat)
            clips_to_composite.append(b_clip)
            
    # 4. Zooms de énfasis (Punch-ins) iterando sobre subclips (MoviePy lo requiere así a veces)
    # Por mantenerlo robusto sin romper el audio en la composición principal,
    # solo simularemos la lógica visual dividiendo el main clip.
    # En producción real de MoviePy, cortamos y concatenamos. Aquí haremos un FX.
    zoom_hooks = [h for h in hooks if h.get("type", "") == "zoom"]
    if zoom_hooks:
        print("Aplicando zooms de énfasis basados en narrativa...")
        final_video_parts = []
        last_t = 0
        
        for zh in sorted(zoom_hooks, key=lambda x: x["start"]):
            # Pedazo normal antes del zoom
            if zh["start"] > last_t:
                normal_part = main_clip.subclip(last_t, zh["start"])
                final_video_parts.append(normal_part)
            
            # El pedazo con zoom
            end_t = min(zh["end"], main_clip.duration)
            zoomed_part = main_clip.subclip(zh["start"], end_t)
            
            # Truco para MoviePy zoom: recortar y redimensionar cuadro a cuadro
            zoomed_part = apply_punch_in(zoomed_part, start_t=0, end_t=end_t-zh["start"])
            final_video_parts.append(zoomed_part)
            
            last_t = end_t
            
        # Añadir resto del video
        if last_t < main_clip.duration:
            final_video_parts.append(main_clip.subclip(last_t, main_clip.duration))
            
        main_clip = concatenate_videoclips(final_video_parts, method="compose")
        # El audio original a veces se pierde en concatenate_videoclips repetido,
        # lo forzamos de vuelta al final.
        clips_to_composite[0] = main_clip

    # 5. Subtítulos dinámicos palabra por palabra
    if whisper_json and os.path.exists(whisper_json):
        print("Generando subtítulos dinámicos sincronizados...")
        subs = create_dynamic_subtitles(whisper_json, clip_size=main_clip.size)
        clips_to_composite.extend(subs)
        
    # 6. Renderizado Final
    print("Renderizando composición final. Esto tomará tiempo de CPU/RAM...")
    final_output = CompositeVideoClip(clips_to_composite)
    # Restore the global audio track to the composite
    final_output = final_output.set_audio(audio_clip)
    
    final_output.write_videofile(output_path, fps=30, codec="libx264", audio_codec="aac", logger=None)
    
    # Cleanup temporal
    for clip in clips_to_composite:
        clip.close()
    print(f"✅ Renderizado finalizado: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Video Editor Core")
    parser.add_argument("--json", required=True, help="Ruta al archivo metadata.json generado por n8n")
    args = parser.parse_args()
    
    process_video(args.json)
