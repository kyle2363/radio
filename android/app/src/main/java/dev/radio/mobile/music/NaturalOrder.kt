package dev.radio.mobile.music

/**
 * Case-insensitive "natural" order: runs of digits compare as numbers, so "song 2" sorts before "song 10". Equal
 * numbers with different padding ("10" and "010") put the shorter spelling first; full ties fall back to plain text.
 */
object NaturalOrder : Comparator<String> {
    override fun compare(a: String, b: String): Int {
        var i = 0
        var j = 0
        while (i < a.length && j < b.length) {
            val ca = a[i]
            val cb = b[j]
            if (ca.isDigit() && cb.isDigit()) {
                val startA = i
                val startB = j
                while (i < a.length && a[i].isDigit()) i++
                while (j < b.length && b[j].isDigit()) j++
                val numberA = a.substring(startA, i).trimStart('0')
                val numberB = b.substring(startB, j).trimStart('0')
                if (numberA.length != numberB.length) return numberA.length - numberB.length
                val byValue = numberA.compareTo(numberB)
                if (byValue != 0) return byValue
                val byPadding = (i - startA) - (j - startB)
                if (byPadding != 0) return byPadding
            } else {
                val byChar = ca.lowercaseChar().compareTo(cb.lowercaseChar())
                if (byChar != 0) return byChar
                i++; j++
            }
        }
        val byLength = (a.length - i) - (b.length - j)
        return if (byLength != 0) byLength else a.compareTo(b)
    }
}
