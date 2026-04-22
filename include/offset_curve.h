#ifndef _OFFSET_CURVE_H
#define _OFFSET_CURVE_H

#include "geom.h"
#include <vector>
#include <string>
#include <cmath>

// Configuration for offset curved label placement
struct OffsetCurveParams {
	double textWidth;           // Estimated width of the label text in coordinate units
	double fontSize;            // Font size in pixels
	double offsetDistance;      // Distance to offset from centerline (0 = on-line mode)
	double charWidth;           // Width per character in pixels

	// Scoring weights
	double flatnessWeight;      // Weight for flatness metric (default 1.0)
	double avdistWeight;        // Weight for average distance metric (default 1.0)
	double centerBiasWeight;    // Weight for center bias (prefer centered candidates, default 0.5)
	double maxAngleDeg;         // Maximum angle delta in degrees (default 45.0)

	// Algorithm parameters
	double simplifyTolerance;   // Douglas-Peucker tolerance for pre-smoothing
	int smoothingIterations;    // Number of Chaikin smoothing passes (default 3)
	double swathMultiplier;     // swath = textWidth * swathMultiplier (default 1.2)
	int stepDivisor;            // step = swath / stepDivisor (default 8)

	OffsetCurveParams();
};

// Result of the placement algorithm
struct PlacementCandidate {
	Linestring guideLine;       // The guide line geometry
	double score;               // Total penalty score
	double flatness;            // Flatness metric value
	double aveDist;             // Average distance metric value
	double maxAngleDelta;       // Maximum angle change in the candidate
	double startLen;            // Start arc-length position
	double endLen;              // End arc-length position
	bool isOffset;              // Whether offset was applied

	PlacementCandidate();
};

// Main entry point: compute the best offset curved label placement.
// Returns true if a valid placement was found (populates `result`).
// Returns false if no suitable candidate exists.
bool computeOffsetCurvePlacement(
	const Linestring& inputLine,
	const OffsetCurveParams& params,
	PlacementCandidate& result
);

// --- Internal algorithm steps ---

// Chaikin's corner-cutting smoothing
Linestring chaikinSmooth(const Linestring& input, int iterations);

// Compute cumulative arc-length parameterization
std::vector<double> computeArcLengths(const Linestring& ls);

// Extract a sub-linestring between two arc-length parameters
Linestring extractSubLine(const Linestring& ls, const std::vector<double>& arcLengths,
                          double startLen, double endLen);

// Compute angle (in degrees) between three points: angle at B in segment A-B-C
double angleBetweenPoints(const Point& a, const Point& b, const Point& c);

// Compute max angle delta along a linestring
double maxAngleDelta(const Linestring& ls);

// Compute flatness metric: area between curve and its chord, normalized
double computeFlatness(const Linestring& ls);

// Offset a linestring perpendicular by distance (positive=left, negative=right)
Linestring offsetLinestring(const Linestring& ls, double distance);

#endif // _OFFSET_CURVE_H
