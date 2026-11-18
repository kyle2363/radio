package dev.radio.mobile.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.unit.dp

/** Rounder Material shapes: cards and sheets read as soft blocks instead of the stock 12 dp corners. */
val RadioMaterialShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(14.dp),
    medium = RoundedCornerShape(20.dp),
    large = RoundedCornerShape(28.dp),
    extraLarge = RoundedCornerShape(36.dp),
)

/** The app's own shape vocabulary. Mixing these on neighbouring controls is deliberate: it is what gives the UI its character. */
object RadioShapes {
    /** The main action (play/pause, Pick for me): wider than tall, heavily rounded. */
    val Squircle = RoundedCornerShape(30.dp)
    /** Secondary tiles and selected-neighbour chips. */
    val Soft = RoundedCornerShape(16.dp)
    /** Asymmetric accent for one chip in a group. */
    val Leaf = RoundedCornerShape(topStart = 22.dp, topEnd = 8.dp, bottomEnd = 22.dp, bottomStart = 8.dp)
    /** Row thumbnails. */
    val Thumb = RoundedCornerShape(14.dp)
}
