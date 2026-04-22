#include "offset_curve.h"
#include <algorithm>
#include <limits>
#include <cmath>
#include <iostream>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// --- OffsetCurveParams defaults ---

OffsetCurveParams::OffsetCurveParams()
	: textWidth(0),
	  fontSize(12),
	  offsetDistance(0),
	  charWidth(7),
	  flatnessWeight(1.0),
	  avdistWeight(1.0),
	  centerBiasWeight(0.5),
	  maxAngleDeg(45.0),
	  simplifyTolerance(0.0002),
	  smoothingIterations(4),
	  swathMultiplier(1.2),
	  stepDivisor(8)
{}

PlacementCandidate::PlacementCandidate()
	: score(std::numeric_limits<double>::infinity()),
	  flatness(0),
	  aveDist(0),
	  maxAngleDelta(0),
	  startLen(0),
	  endLen(0),
	  isOffset(false)
{}

// --- Step 1: Chaikin's corner-cutting smoothing ---
// For each segment (P[i], P[i+1]), insert points at 1/4 and 3/4 of the segment.
// Repeat for N iterations. Keeps first and last points.

Linestring chaikinSmooth(const Linestring& input, int iterations) {
	if (input.size() < 3 || iterations <= 0) return input;

	Linestring current = input;

	for (int iter = 0; iter < iterations; iter++) {
		Linestring next;
		next.reserve(current.size() * 2);
		next.push_back(current.front()); // keep first point

		for (size_t i = 0; i + 1 < current.size(); i++) {
			double x0 = current[i].x(), y0 = current[i].y();
			double x1 = current[i+1].x(), y1 = current[i+1].y();
			next.push_back(Point(0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1));
			next.push_back(Point(0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1));
		}

		next.push_back(current.back()); // keep last point
		current = std::move(next);
	}

	return current;
}

// --- Arc-length parameterization ---

std::vector<double> computeArcLengths(const Linestring& ls) {
	std::vector<double> lengths;
	lengths.reserve(ls.size());
	lengths.push_back(0.0);
	for (size_t i = 1; i < ls.size(); i++) {
		double dx = ls[i].x() - ls[i-1].x();
		double dy = ls[i].y() - ls[i-1].y();
		double segLen = std::sqrt(dx*dx + dy*dy);
		lengths.push_back(lengths.back() + segLen);
	}
	return lengths;
}

// --- Sub-line extraction by arc-length ---

Linestring extractSubLine(const Linestring& ls, const std::vector<double>& arcLengths,
                          double startLen, double endLen) {
	Linestring result;
	if (startLen >= endLen || ls.size() < 2) return result;

	// Interpolate a point at a given arc-length along the line
	auto interpolate = [&](double len) -> Point {
		// Binary search for the segment containing this length
		auto it = std::lower_bound(arcLengths.begin(), arcLengths.end(), len);
		size_t idx = std::distance(arcLengths.begin(), it);

		if (idx == 0) return ls.front();
		if (idx >= ls.size()) return ls.back();

		double segStart = arcLengths[idx - 1];
		double segEnd = arcLengths[idx];
		double t = (segEnd > segStart) ? (len - segStart) / (segEnd - segStart) : 0.0;
		t = std::max(0.0, std::min(1.0, t));

		return Point(
			ls[idx-1].x() + t * (ls[idx].x() - ls[idx-1].x()),
			ls[idx-1].y() + t * (ls[idx].y() - ls[idx-1].y())
		);
	};

	// Add interpolated start point
	result.push_back(interpolate(startLen));

	// Add all interior points within the range
	for (size_t i = 0; i < ls.size(); i++) {
		if (arcLengths[i] > startLen && arcLengths[i] < endLen) {
			result.push_back(ls[i]);
		}
	}

	// Add interpolated end point
	result.push_back(interpolate(endLen));

	return result;
}

// --- Angle computation ---

double angleBetweenPoints(const Point& a, const Point& b, const Point& c) {
	double v1x = a.x() - b.x(), v1y = a.y() - b.y();
	double v2x = c.x() - b.x(), v2y = c.y() - b.y();

	double dot = v1x * v2x + v1y * v2y;
	double cross = v1x * v2y - v1y * v2x;

	double len1 = std::sqrt(v1x*v1x + v1y*v1y);
	double len2 = std::sqrt(v2x*v2x + v2y*v2y);
	if (len1 < 1e-15 || len2 < 1e-15) return 0.0;

	// Interior angle (0-180°). 180° = straight line, 0° = U-turn.
	double cosA = dot / (len1 * len2);
	cosA = std::max(-1.0, std::min(1.0, cosA));
	double interiorAngle = std::acos(cosA) * 180.0 / M_PI;

	// Return TURN angle: deviation from straight line.
	// 0° = straight, 90° = right angle turn, 180° = U-turn
	return 180.0 - interiorAngle;
}

double maxAngleDelta(const Linestring& ls) {
	if (ls.size() < 3) return 0.0;

	double maxAngle = 0.0;
	for (size_t i = 1; i + 1 < ls.size(); i++) {
		double angle = angleBetweenPoints(ls[i-1], ls[i], ls[i+1]);
		maxAngle = std::max(maxAngle, angle);
	}
	return maxAngle;
}

// --- Flatness metric ---
// Area between the curve and its chord (straight line between endpoints), normalized by length.

double computeFlatness(const Linestring& ls) {
	if (ls.size() < 3) return 0.0;

	// Build a closed polygon from the curve + chord
	Polygon p;
	for (const auto& pt : ls) {
		boost::geometry::append(p.outer(), pt);
	}
	boost::geometry::append(p.outer(), ls.front()); // close with chord
	boost::geometry::correct(p);

	double area = std::abs(boost::geometry::area(p));

	// Normalize by chord length
	double dx = ls.back().x() - ls.front().x();
	double dy = ls.back().y() - ls.front().y();
	double chordLen = std::sqrt(dx*dx + dy*dy);

	if (chordLen < 1e-10) return std::numeric_limits<double>::infinity();
	return area / (chordLen * chordLen);
}

// --- Offset curve ---

Linestring offsetLinestring(const Linestring& ls, double distance) {
	if (ls.size() < 2 || std::abs(distance) < 1e-10) return ls;

	Linestring result;
	result.reserve(ls.size());

	for (size_t i = 0; i < ls.size(); i++) {
		double nx, ny;

		if (i == 0) {
			// First point: normal to first segment
			double dx = ls[1].x() - ls[0].x();
			double dy = ls[1].y() - ls[0].y();
			double len = std::sqrt(dx*dx + dy*dy);
			if (len < 1e-10) { result.push_back(ls[i]); continue; }
			// Perpendicular (rotate 90° left)
			nx = -dy / len;
			ny = dx / len;
		} else if (i + 1 == ls.size()) {
			// Last point: normal to last segment
			double dx = ls[i].x() - ls[i-1].x();
			double dy = ls[i].y() - ls[i-1].y();
			double len = std::sqrt(dx*dx + dy*dy);
			if (len < 1e-10) { result.push_back(ls[i]); continue; }
			nx = -dy / len;
			ny = dx / len;
		} else {
			// Interior point: average of adjacent segment normals
			double dx1 = ls[i].x() - ls[i-1].x();
			double dy1 = ls[i].y() - ls[i-1].y();
			double len1 = std::sqrt(dx1*dx1 + dy1*dy1);

			double dx2 = ls[i+1].x() - ls[i].x();
			double dy2 = ls[i+1].y() - ls[i].y();
			double len2 = std::sqrt(dx2*dx2 + dy2*dy2);

			if (len1 < 1e-10 || len2 < 1e-10) { result.push_back(ls[i]); continue; }

			// Normals (perpendicular left)
			double n1x = -dy1 / len1, n1y = dx1 / len1;
			double n2x = -dy2 / len2, n2y = dx2 / len2;

			// Average and renormalize
			nx = n1x + n2x;
			ny = n1y + n2y;
			double nlen = std::sqrt(nx*nx + ny*ny);
			if (nlen < 1e-10) { result.push_back(ls[i]); continue; }
			nx /= nlen;
			ny /= nlen;
		}

		result.push_back(Point(ls[i].x() + distance * nx, ls[i].y() + distance * ny));
	}

	return result;
}

// Determine which side of the linestring is the outer (convex) side.
// Returns +1.0 for left, -1.0 for right.
static double outerOffsetDir(const Linestring& ls) {
	if (ls.size() < 2) return 1.0;
	double signedArea = 0;
	for (size_t i = 0; i + 1 < ls.size(); i++) {
		signedArea += (ls[i+1].x() - ls[i].x()) * (ls[i+1].y() + ls[i].y());
	}
	// Close with chord (end -> start)
	signedArea += (ls.front().x() - ls.back().x()) * (ls.front().y() + ls.back().y());
	// positive signedArea -> curve bulges left of travel -> outer is left -> +1
	// negative signedArea -> curve bulges right of travel -> outer is right -> -1
	return (signedArea >= 0) ? 1.0 : -1.0;
}

// Apply offset to the outer (convex) side of the linestring.
// Falls back to inner side if self-intersection occurs.
static Linestring applyOuterOffset(const Linestring& ls, double distance) {
	double dir = outerOffsetDir(ls);
	Linestring result = offsetLinestring(ls, dir * distance);
	if (result.size() < 2) {
		result = offsetLinestring(ls, -dir * distance);
	}
	return result;
}

// --- Main algorithm ---

bool computeOffsetCurvePlacement(
	const Linestring& inputLine,
	const OffsetCurveParams& params,
	PlacementCandidate& result
) {
	if (inputLine.size() < 2) return false;

	// Step 1: Simplify + Smooth
	Linestring simplified;
	if (params.simplifyTolerance > 0) {
		boost::geometry::simplify(inputLine, simplified, params.simplifyTolerance);
	} else {
		simplified = inputLine;
	}

	if (simplified.size() < 2) return false;

	Linestring smoothed = chaikinSmooth(simplified, params.smoothingIterations);
	if (smoothed.size() < 2) return false;

	// Arc-length parameterization
	std::vector<double> arcLengths = computeArcLengths(smoothed);
	double totalLen = arcLengths.back();
	if (totalLen < 1e-10) return false;

	// Required window (swath) length — cap at 90% of line length so we always find something
	double swath = params.textWidth * params.swathMultiplier;
	if (swath <= 0) return false;

	// If the line is shorter than the text, use the full smoothed line as guide.
	// The renderer will truncate text that doesn't fit.
	if (swath > totalLen) {
		PlacementCandidate c;
		c.guideLine = smoothed;
		c.score = 0;
		c.flatness = 0;
		c.aveDist = 0;
		c.maxAngleDelta = maxAngleDelta(smoothed);
		c.startLen = 0;
		c.endLen = totalLen;
		c.isOffset = false;

		if (params.offsetDistance > 0) {
			c.guideLine = applyOuterOffset(smoothed, params.offsetDistance);
			c.isOffset = true;
		}

		result = std::move(c);
		return true;
	}

	if (swath > totalLen * 0.9) {
		swath = totalLen * 0.9;
	}

	double step = swath / params.stepDivisor;
	if (step < 1e-10) step = swath * 0.01; // minimum step

	extern bool verbose;
	if (verbose) {
		std::cout << "  algo: totalLen=" << totalLen << " swath=" << swath
		          << " step=" << step << " smoothedPts=" << smoothed.size() << std::endl;
	}

	// Step 2: Generate candidates
	std::vector<PlacementCandidate> candidates;
	int angleDiscarded = 0;

	for (double start = 0; start + swath <= totalLen + 1e-10; start += step) {
		double end = std::min(start + swath, totalLen);

		Linestring candidate = extractSubLine(smoothed, arcLengths, start, end);
		if (candidate.size() < 2) continue;

		// Step 3: Score candidate

		// Max angle check (discard if exceeded)
		double maxAngle = maxAngleDelta(candidate);
		if (maxAngle > params.maxAngleDeg) {
			angleDiscarded++;
			continue;
		}

		// Flatness metric
		double flatness = computeFlatness(candidate);

		// Average distance metric (for offset mode)
		double aveDistPenalty = 0.0;
		if (params.offsetDistance > 0) {
			// Compute average distance from candidate to the original line
			double sumDist = 0.0;
			for (const auto& pt : candidate) {
				double minDist = std::numeric_limits<double>::infinity();
				for (size_t j = 0; j + 1 < inputLine.size(); j++) {
					// Point-to-segment distance
					double ax = inputLine[j].x(), ay = inputLine[j].y();
					double bx = inputLine[j+1].x(), by = inputLine[j+1].y();
					double px = pt.x(), py = pt.y();

					double abx = bx - ax, aby = by - ay;
					double apx = px - ax, apy = py - ay;
					double t = (apx*abx + apy*aby) / (abx*abx + aby*aby + 1e-30);
					t = std::max(0.0, std::min(1.0, t));

					double closestX = ax + t * abx;
					double closestY = ay + t * aby;
					double dx = px - closestX, dy = py - closestY;
					double dist = std::sqrt(dx*dx + dy*dy);
					minDist = std::min(minDist, dist);
				}
				sumDist += minDist;
			}
			double avgDist = sumDist / candidate.size();
			double delta = params.offsetDistance;
			if (delta > 1e-10) {
				aveDistPenalty = (avgDist - delta) * (avgDist - delta) / (delta * delta);
			}
		}

		double score = params.flatnessWeight * flatness + params.avdistWeight * aveDistPenalty;

		// Center bias: prefer candidates close to the middle of the line
		if (params.centerBiasWeight > 0) {
			double candidateCenter = (start + end) / 2.0;
			double lineCenter = totalLen / 2.0;
			double centerPenalty = (candidateCenter - lineCenter) / totalLen;
			centerPenalty = centerPenalty * centerPenalty;
			score += params.centerBiasWeight * centerPenalty;
		}

		PlacementCandidate c;
		c.guideLine = candidate;
		c.score = score;
		c.flatness = flatness;
		c.aveDist = aveDistPenalty;
		c.maxAngleDelta = maxAngle;
		c.startLen = start;
		c.endLen = end;
		c.isOffset = false;
		candidates.push_back(std::move(c));
	}

	if (candidates.empty()) {
		if (verbose) {
			std::cout << "  algo: no candidates, angleDiscarded=" << angleDiscarded << std::endl;
		}
		return false;
	}

	// Select best candidate (lowest score)
	size_t bestIdx = 0;
	for (size_t i = 1; i < candidates.size(); i++) {
		if (candidates[i].score < candidates[bestIdx].score) {
			bestIdx = i;
		}
	}

		result = std::move(candidates[bestIdx]);


		// Step 4: Apply offset to outer side
		if (params.offsetDistance > 0 && result.guideLine.size() >= 2) {
			result.guideLine = applyOuterOffset(result.guideLine, params.offsetDistance);
			result.isOffset = true;
		}

		return true;
	}
