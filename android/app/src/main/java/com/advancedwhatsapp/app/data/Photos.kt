package com.advancedwhatsapp.app.data

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.util.Base64
import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.gateway.GatewayException
import java.io.ByteArrayOutputStream
import java.io.File
import kotlin.math.max
import kotlin.math.roundToInt

/** Fotos tomadas con la cámara y miniaturas de lo que se envía. Bloqueante: llamar desde Dispatchers.IO. */
object Photos {
    private const val MAX_SIDE = 1600
    private const val JPEG_QUALITY = 82
    private const val THUMB_SIDE = 96
    private const val THUMB_QUALITY = 60

    fun newCameraFile(): File = File(App.sentDir, "camara_${System.currentTimeMillis()}.jpg")

    /**
     * Deja la foto lista para enviar: más liviana, derecha y sin EXIF.
     * La cámara guarda el giro como dato aparte, y además anota dónde se sacó la foto.
     */
    fun prepare(file: File) {
        val bitmap = decode(file, MAX_SIDE) ?: throw GatewayException("La cámara no entregó una foto válida.")
        val straight = applyOrientation(bitmap, runCatching {
            ExifInterface(file.path).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
        }.getOrDefault(ExifInterface.ORIENTATION_NORMAL))
        val tmp = File(file.path + ".tmp")
        tmp.outputStream().use { straight.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, it) }
        if (!tmp.renameTo(file)) {
            tmp.delete()
            throw GatewayException("No se pudo guardar la foto.")
        }
    }

    /** WhatsApp la muestra mientras se baja la foto. En el celular Baileys no tiene con qué generarla. */
    fun thumbnailBase64(file: File): String? = runCatching {
        val bitmap = decode(file, THUMB_SIDE) ?: return null
        val out = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, THUMB_QUALITY, out)
        Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }.getOrNull()

    /** El mayor divisor (potencia de 2) que deja la imagen igual o más grande que maxSide. */
    fun sampleSize(width: Int, height: Int, maxSide: Int): Int {
        var sample = 1
        while (max(width, height) / (sample * 2) >= maxSide) sample *= 2
        return sample
    }

    /** Imagen decodificada y achicada a [maxSide] como máximo (la usan las miniaturas y Leer imágenes). */
    fun decode(file: File, maxSide: Int): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.path, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        val options = BitmapFactory.Options().apply { inSampleSize = sampleSize(bounds.outWidth, bounds.outHeight, maxSide) }
        val raw = BitmapFactory.decodeFile(file.path, options) ?: return null
        val side = max(raw.width, raw.height)
        if (side <= maxSide) return raw
        val ratio = maxSide.toFloat() / side
        return Bitmap.createScaledBitmap(raw, (raw.width * ratio).roundToInt(), (raw.height * ratio).roundToInt(), true)
    }

    private fun applyOrientation(bitmap: Bitmap, orientation: Int): Bitmap {
        val m = Matrix()
        when (orientation) {
            ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> m.setScale(-1f, 1f)
            ExifInterface.ORIENTATION_ROTATE_180 -> m.setRotate(180f)
            ExifInterface.ORIENTATION_FLIP_VERTICAL -> m.setScale(1f, -1f)
            ExifInterface.ORIENTATION_TRANSPOSE -> {
                m.setRotate(90f)
                m.postScale(-1f, 1f)
            }
            ExifInterface.ORIENTATION_ROTATE_90 -> m.setRotate(90f)
            ExifInterface.ORIENTATION_TRANSVERSE -> {
                m.setRotate(-90f)
                m.postScale(-1f, 1f)
            }
            ExifInterface.ORIENTATION_ROTATE_270 -> m.setRotate(-90f)
            else -> return bitmap
        }
        return Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, m, true)
    }
}
