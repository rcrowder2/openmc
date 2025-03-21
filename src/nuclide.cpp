#include "openmc/nuclide.h"

#include "openmc/capi.h"
#include "openmc/container_util.h"
#include "openmc/cross_sections.h"
#include "openmc/endf.h"
#include "openmc/error.h"
#include "openmc/hdf5_interface.h"
#include "openmc/message_passing.h"
#include "openmc/photon.h"
#include "openmc/random_lcg.h"
#include "openmc/search.h"
#include "openmc/settings.h"
#include "openmc/simulation.h"
#include "openmc/string_utils.h"
#include "openmc/thermal.h"

#include <fmt/core.h>

#include "xtensor/xbuilder.hpp"
#include "xtensor/xview.hpp"

#include <algorithm> // for sort, min_element
#include <cassert>
#include <string> // for to_string, stoi

namespace openmc {

//==============================================================================
// Global variables
//==============================================================================

namespace data {
array<double, 2> energy_min {0.0, 0.0};
array<double, 2> energy_max {INFTY, INFTY};
double temperature_min {INFTY};
double temperature_max {0.0};
std::unordered_map<std::string, int> nuclide_map;
vector<unique_ptr<Nuclide>> nuclides;
} // namespace data

//==============================================================================
// Nuclide implementation
//==============================================================================

int Nuclide::XS_TOTAL {0};
int Nuclide::XS_ABSORPTION {1};
int Nuclide::XS_FISSION {2};
int Nuclide::XS_NU_FISSION {3};
int Nuclide::XS_PHOTON_PROD {4};

Nuclide::Nuclide(hid_t group, const vector<double>& temperature)
{
  // Set index of nuclide in global vector
  index_ = data::nuclides.size();

  // Get name of nuclide from group, removing leading '/'
  name_ = object_name(group).substr(1);
  data::nuclide_map[name_] = index_;

  read_attribute(group, "Z", Z_);
  read_attribute(group, "A", A_);
  read_attribute(group, "metastable", metastable_);
  read_attribute(group, "atomic_weight_ratio", awr_);

  if (settings::run_mode == RunMode::VOLUME) {
    // Determine whether nuclide is fissionable and then exit
    int mt;
    hid_t rxs_group = open_group(group, "reactions");
    for (auto name : group_names(rxs_group)) {
      if (starts_with(name, "reaction_")) {
        hid_t rx_group = open_group(rxs_group, name.c_str());
        read_attribute(rx_group, "mt", mt);
        if (is_fission(mt)) {
          fissionable_ = true;
          break;
        }
      }
    }
    return;
  }

  // Determine temperatures available
  hid_t kT_group = open_group(group, "kTs");
  auto dset_names = dataset_names(kT_group);
  vector<double> temps_available;
  for (const auto& name : dset_names) {
    double T;
    read_dataset(kT_group, name.c_str(), T);
    temps_available.push_back(std::round(T / K_BOLTZMANN));
  }
  std::sort(temps_available.begin(), temps_available.end());

  // If only one temperature is available, revert to nearest temperature
  if (temps_available.size() == 1 &&
      settings::temperature_method == TemperatureMethod::INTERPOLATION) {
    if (mpi::master) {
      warning("Cross sections for " + name_ +
              " are only available at one "
              "temperature. Reverting to nearest temperature method.");
    }
    settings::temperature_method = TemperatureMethod::NEAREST;
  }

  // Determine actual temperatures to read -- start by checking whether a
  // temperature range was given (indicated by T_max > 0), in which case all
  // temperatures in the range are loaded irrespective of what temperatures
  // actually appear in the model
  vector<int> temps_to_read;
  int n = temperature.size();
  double T_min = n > 0 ? settings::temperature_range[0] : 0.0;
  double T_max = n > 0 ? settings::temperature_range[1] : INFTY;
  if (T_max > 0.0) {
    // Determine first available temperature below or equal to T_min
    auto T_min_it =
      std::upper_bound(temps_available.begin(), temps_available.end(), T_min);
    if (T_min_it != temps_available.begin())
      --T_min_it;

    // Determine first available temperature above or equal to T_max
    auto T_max_it =
      std::lower_bound(temps_available.begin(), temps_available.end(), T_max);
    if (T_max_it != temps_available.end())
      ++T_max_it;

    // Add corresponding temperatures to vector
    for (auto it = T_min_it; it != T_max_it; ++it) {
      temps_to_read.push_back(std::round(*it));
    }
  }

  switch (settings::temperature_method) {
  case TemperatureMethod::NEAREST:
    // Find nearest temperatures
    for (double T_desired : temperature) {

      // Determine closest temperature
      double min_delta_T = INFTY;
      double T_actual = 0.0;
      for (auto T : temps_available) {
        double delta_T = std::abs(T - T_desired);
        if (delta_T < min_delta_T) {
          T_actual = T;
          min_delta_T = delta_T;
        }
      }

      if (std::abs(T_actual - T_desired) < settings::temperature_tolerance) {
        if (!contains(temps_to_read, std::round(T_actual))) {
          temps_to_read.push_back(std::round(T_actual));

          // Write warning for resonance scattering data if 0K is not available
          if (std::abs(T_actual - T_desired) > 0 && T_desired == 0 &&
              mpi::master) {
            warning(name_ +
                    " does not contain 0K data needed for resonance "
                    "scattering options selected. Using data at " +
                    std::to_string(T_actual) + " K instead.");
          }
        }
      } else {
        fatal_error(fmt::format(
          "Nuclear data library does not contain cross sections "
          "for {}  at or near {} K. Available temperatures "
          "are {} K. Consider making use of openmc.Settings.temperature "
          "to specify how intermediate temperatures are treated.",
          name_, std::to_string(T_desired), concatenate(temps_available)));
      }
    }
    break;

  case TemperatureMethod::INTERPOLATION:
    // If temperature interpolation or multipole is selected, get a list of
    // bounding temperatures for each actual temperature present in the model
    for (double T_desired : temperature) {
      bool found_pair = false;
      for (int j = 0; j < temps_available.size() - 1; ++j) {
        if (temps_available[j] <= T_desired &&
            T_desired < temps_available[j + 1]) {
          int T_j = temps_available[j];
          int T_j1 = temps_available[j + 1];
          if (!contains(temps_to_read, T_j)) {
            temps_to_read.push_back(T_j);
          }
          if (!contains(temps_to_read, T_j1)) {
            temps_to_read.push_back(T_j1);
          }
          found_pair = true;
        }
      }

      if (!found_pair) {
        // If no pairs found, check if the desired temperature falls just
        // outside of data
        if (std::abs(T_desired - temps_available.front()) <=
            settings::temperature_tolerance) {
          if (!contains(temps_to_read, temps_available.front())) {
            temps_to_read.push_back(temps_available.front());
          }
          continue;
        }
        if (std::abs(T_desired - temps_available.back()) <=
            settings::temperature_tolerance) {
          if (!contains(temps_to_read, temps_available.back())) {
            temps_to_read.push_back(temps_available.back());
          }
          continue;
        }
        fatal_error(
          "Nuclear data library does not contain cross sections for " + name_ +
          " at temperatures that bound " + std::to_string(T_desired) + " K.");
      }
    }
    break;
  }

  // Sort temperatures to read
  std::sort(temps_to_read.begin(), temps_to_read.end());

  data::temperature_min =
    std::min(data::temperature_min, static_cast<double>(temps_to_read.front()));
  data::temperature_max =
    std::max(data::temperature_max, static_cast<double>(temps_to_read.back()));

  hid_t energy_group = open_group(group, "energy");
  for (const auto& T : temps_to_read) {
    std::string dset {std::to_string(T) + "K"};

    // Determine exact kT values
    double kT;
    read_dataset(kT_group, dset.c_str(), kT);
    kTs_.push_back(kT);

    // Read energy grid
    grid_.emplace_back();
    read_dataset(energy_group, dset.c_str(), grid_.back().energy);
  }
  close_group(kT_group);

  // Check for 0K energy grid
  if (object_exists(energy_group, "0K")) {
    read_dataset(energy_group, "0K", energy_0K_);
  }
  close_group(energy_group);

  // Read reactions
  hid_t rxs_group = open_group(group, "reactions");
  for (auto name : group_names(rxs_group)) {
    if (starts_with(name, "reaction_")) {
      hid_t rx_group = open_group(rxs_group, name.c_str());
      reactions_.push_back(
        make_unique<Reaction>(rx_group, temps_to_read, name_));

      // Check for 0K elastic scattering
      const auto& rx = reactions_.back();
      if (rx->mt_ == ELASTIC) {
        if (object_exists(rx_group, "0K")) {
          hid_t temp_group = open_group(rx_group, "0K");
          read_dataset(temp_group, "xs", elastic_0K_);
          close_group(temp_group);
        }
      }
      close_group(rx_group);

      // Determine reaction indices for inelastic scattering reactions
      if (is_inelastic_scatter(rx->mt_) && !rx->redundant_) {
        index_inelastic_scatter_.push_back(reactions_.size() - 1);
      }
    }
  }
  close_group(rxs_group);

  // Read unresolved resonance probability tables if present
  if (object_exists(group, "urr")) {
    urr_present_ = true;
    urr_data_.reserve(temps_to_read.size());

    for (int i = 0; i < temps_to_read.size(); i++) {
      // Get temperature as a string
      std::string temp_str {std::to_string(temps_to_read[i]) + "K"};

      // Read probability tables for i-th temperature
      hid_t urr_group = open_group(group, ("urr/" + temp_str).c_str());
      urr_data_.emplace_back(urr_group);
      close_group(urr_group);

      // Check for negative values
      if (urr_data_[i].has_negative() && mpi::master) {
        warning("Negative value(s) found on probability table for nuclide " +
                name_ + " at " + temp_str);
      }
    }

    // If the inelastic competition flag indicates that the inelastic cross
    // section should be determined from a normal reaction cross section, we
    // need to get the index of the reaction.
    if (temps_to_read.size() > 0) {
      // Make sure inelastic flags are consistent for different temperatures
      for (int i = 0; i < urr_data_.size() - 1; ++i) {
        if (urr_data_[i].inelastic_flag_ != urr_data_[i + 1].inelastic_flag_) {
          fatal_error(fmt::format(
            "URR inelastic flag is not consistent for "
            "multiple temperatures in nuclide {}. This most likely indicates "
            "a problem in how the data was processed.",
            name_));
        }
      }

      if (urr_data_[0].inelastic_flag_ > 0) {
        for (int i = 0; i < reactions_.size(); i++) {
          if (reactions_[i]->mt_ == urr_data_[0].inelastic_flag_) {
            urr_inelastic_ = i;
          }
        }

        // Abort if no corresponding inelastic reaction was found
        if (urr_inelastic_ == C_NONE) {
          fatal_error("Could no find inelastic reaction specified on "
                      "unresolved resonance probability table.");
        }
      }
    }
  }

  // Check for total nu data
  if (object_exists(group, "total_nu")) {
    // Read total nu data
    hid_t nu_group = open_group(group, "total_nu");
    total_nu_ = read_function(nu_group, "yield");
    close_group(nu_group);
  }

  // Read fission energy release data if present
  if (object_exists(group, "fission_energy_release")) {
    hid_t fer_group = open_group(group, "fission_energy_release");
    fission_q_prompt_ = read_function(fer_group, "q_prompt");
    fission_q_recov_ = read_function(fer_group, "q_recoverable");

    // Read fission fragment and delayed beta energy release. This is needed for
    // energy normalization in k-eigenvalue calculations
    fragments_ = read_function(fer_group, "fragments");
    betas_ = read_function(fer_group, "betas");

    // We need prompt/delayed photon energy release for scaling fission photon
    // production
    prompt_photons_ = read_function(fer_group, "prompt_photons");
    delayed_photons_ = read_function(fer_group, "delayed_photons");
    close_group(fer_group);
  }

  this->create_derived(prompt_photons_.get(), delayed_photons_.get());
}

Nuclide::~Nuclide()
{
  data::nuclide_map.erase(name_);
}

void Nuclide::create_derived(
  const Function1D* prompt_photons, const Function1D* delayed_photons)
{
  for (const auto& grid : grid_) {
    // Allocate and initialize cross section
    array<size_t, 2> shape {grid.energy.size(), 5};
    xs_.emplace_back(shape, 0.0);
  }

  reaction_index_.fill(C_NONE);
  for (int i = 0; i < reactions_.size(); ++i) {
    const auto& rx {reactions_[i]};

    // Set entry in direct address table for reaction
    reaction_index_[rx->mt_] = i;

    for (int t = 0; t < kTs_.size(); ++t) {
      int j = rx->xs_[t].threshold;
      int n = rx->xs_[t].value.size();
      auto xs = xt::adapt(rx->xs_[t].value);
      auto pprod = xt::view(xs_[t], xt::range(j, j + n), XS_PHOTON_PROD);

      for (const auto& p : rx->products_) {
        if (p.particle_ == ParticleType::photon) {
          for (int k = 0; k < n; ++k) {
            double E = grid_[t].energy[k + j];

            // For fission, artificially increase the photon yield to
            // account for delayed photons
            double f = 1.0;
            if (settings::delayed_photon_scaling) {
              if (is_fission(rx->mt_)) {
                if (prompt_photons && delayed_photons) {
                  double energy_prompt = (*prompt_photons)(E);
                  double energy_delayed = (*delayed_photons)(E);
                  f = (energy_prompt + energy_delayed) / (energy_prompt);
                }
              }
            }

            pprod[k] += f * xs[k] * (*p.yield_)(E);
          }
        }
      }

      // Skip redundant reactions
      if (rx->redundant_)
        continue;

      // Add contribution to total cross section
      auto total = xt::view(xs_[t], xt::range(j, j + n), XS_TOTAL);
      total += xs;

      // Add contribution to absorption cross section
      auto absorption = xt::view(xs_[t], xt::range(j, j + n), XS_ABSORPTION);
      if (is_disappearance(rx->mt_)) {
        absorption += xs;
      }

      if (is_fission(rx->mt_)) {
        fissionable_ = true;
        auto fission = xt::view(xs_[t], xt::range(j, j + n), XS_FISSION);
        fission += xs;
        absorption += xs;

        // Keep track of fission reactions
        if (t == 0) {
          fission_rx_.push_back(rx.get());
          if (rx->mt_ == N_F)
            has_partial_fission_ = true;
        }
      }
    }
  }

  // Determine number of delayed neutron precursors
  if (fissionable_) {
    for (const auto& product : fission_rx_[0]->products_) {
      if (product.emission_mode_ == EmissionMode::delayed) {
        ++n_precursor_;
      }
    }
  }

  // Calculate nu-fission cross section
  for (int t = 0; t < kTs_.size(); ++t) {
    if (fissionable_) {
      int n = grid_[t].energy.size();
      for (int i = 0; i < n; ++i) {
        double E = grid_[t].energy[i];
        xs_[t](i, XS_NU_FISSION) =
          nu(E, EmissionMode::total) * xs_[t](i, XS_FISSION);
      }
    }
  }

  if (settings::res_scat_on) {
    // Determine if this nuclide should be treated as a resonant scatterer
    if (!settings::res_scat_nuclides.empty()) {
      // If resonant nuclides were specified, check the list explicitly
      for (const auto& name : settings::res_scat_nuclides) {
        if (name_ == name) {
          resonant_ = true;

          // Make sure nuclide has 0K data
          if (energy_0K_.empty()) {
            fatal_error("Cannot treat " + name_ +
                        " as a resonant scatterer "
                        "because 0 K elastic scattering data is not present.");
          }
          break;
        }
      }
    } else {
      // Otherwise, assume that any that have 0 K elastic scattering data
      // are resonant
      resonant_ = !energy_0K_.empty();
    }

    if (resonant_) {
      // Build CDF for 0K elastic scattering
      double xs_cdf_sum = 0.0;
      xs_cdf_.resize(energy_0K_.size());
      xs_cdf_[0] = 0.0;

      const auto& E = energy_0K_;
      auto& xs = elastic_0K_;
      for (int i = 0; i < E.size() - 1; ++i) {
        // Negative cross sections result in a CDF that is not monotonically
        // increasing. Set all negative xs values to zero.
        if (xs[i] < 0.0)
          xs[i] = 0.0;

        // build xs cdf
        xs_cdf_sum +=
          (std::sqrt(E[i]) * xs[i] + std::sqrt(E[i + 1]) * xs[i + 1]) / 2.0 *
          (E[i + 1] - E[i]);
        xs_cdf_[i + 1] = xs_cdf_sum;
      }
    }
  }
}

void Nuclide::init_grid()
{
  int neutron = static_cast<int>(ParticleType::neutron);
  double E_min = data::energy_min[neutron];
  double E_max = data::energy_max[neutron];
  int M = settings::n_log_bins;

  // Determine equal-logarithmic energy spacing
  double spacing = std::log(E_max / E_min) / M;

  // Create equally log-spaced energy grid
  auto umesh = xt::linspace(0.0, M * spacing, M + 1);

  for (auto& grid : grid_) {
    // Resize array for storing grid indices
    grid.grid_index.resize(M + 1);

    // Determine corresponding indices in nuclide grid to energies on
    // equal-logarithmic grid
    int j = 0;
    for (int k = 0; k <= M; ++k) {
      while (std::log(grid.energy[j + 1] / E_min) <= umesh(k)) {
        // Ensure that for isotopes where maxval(grid.energy) << E_max that
        // there are no out-of-bounds issues.
        if (j + 2 == grid.energy.size())
          break;
        ++j;
      }
      grid.grid_index[k] = j;
    }
  }
}

double Nuclide::nu(double E, EmissionMode mode, int group) const
{
  if (!fissionable_)
    return 0.0;

  switch (mode) {
  case EmissionMode::prompt:
    return (*fission_rx_[0]->products_[0].yield_)(E);
  case EmissionMode::delayed:
    if (n_precursor_ > 0 && settings::create_delayed_neutrons) {
      auto rx = fission_rx_[0];
      if (group >= 1 && group < rx->products_.size()) {
        // If delayed group specified, determine yield immediately
        return (*rx->products_[group].yield_)(E);
      } else {
        double nu {0.0};

        for (int i = 1; i < rx->products_.size(); ++i) {
          // Skip any non-neutron products
          const auto& product = rx->products_[i];
          if (product.particle_ != ParticleType::neutron)
            continue;

          // Evaluate yield
          if (product.emission_mode_ == EmissionMode::delayed) {
            nu += (*product.yield_)(E);
          }
        }
        return nu;
      }
    } else {
      return 0.0;
    }
  case EmissionMode::total:
    if (total_nu_ && settings::create_delayed_neutrons) {
      return (*total_nu_)(E);
    } else {
      return (*fission_rx_[0]->products_[0].yield_)(E);
    }
  }
  UNREACHABLE();
}

void Nuclide::calculate_elastic_xs(Particle& p) const
{
  // Get temperature index, grid index, and interpolation factor
  auto& micro {p.neutron_xs(index_)};
  int i_temp = micro.index_temp;
  int i_grid = micro.index_grid;
  double f = micro.interp_factor;

  if (i_temp >= 0) {
    const auto& xs = reactions_[0]->xs_[i_temp].value;
    micro.elastic = (1.0 - f) * xs[i_grid] + f * xs[i_grid + 1];
  }
}

double Nuclide::elastic_xs_0K(double E) const
{
  // Determine index on nuclide energy grid
  int i_grid;
  if (E < energy_0K_.front()) {
    i_grid = 0;
  } else if (E > energy_0K_.back()) {
    i_grid = energy_0K_.size() - 2;
  } else {
    i_grid = lower_bound_index(energy_0K_.begin(), energy_0K_.end(), E);
  }

  // check for rare case where two energy points are the same
  if (energy_0K_[i_grid] == energy_0K_[i_grid + 1])
    ++i_grid;

  // calculate interpolation factor
  double f =
    (E - energy_0K_[i_grid]) / (energy_0K_[i_grid + 1] - energy_0K_[i_grid]);

  // Calculate microscopic nuclide elastic cross section
  return (1.0 - f) * elastic_0K_[i_grid] + f * elastic_0K_[i_grid + 1];
}

void Nuclide::calculate_xs(
  int i_sab, int i_log_union, double sab_frac, Particle& p)
{
  auto& micro {p.neutron_xs(index_)};

  // Initialize cached cross sections to zero
  micro.elastic = CACHE_INVALID;
  micro.thermal = 0.0;
  micro.thermal_elastic = 0.0;

  // Check to see if there is multipole data present at this energy
  bool use_mp = false;
  if (multipole_) {
    use_mp = (p.E() >= multipole_->E_min_ && p.E() <= multipole_->E_max_);
  }

  // Evaluate multipole or interpolate
  if (use_mp) {
    // Call multipole kernel
    double sig_s, sig_a, sig_f;
    std::tie(sig_s, sig_a, sig_f) = multipole_->evaluate(p.E(), p.sqrtkT());

    micro.total = sig_s + sig_a;
    micro.elastic = sig_s;
    micro.absorption = sig_a;
    micro.fission = sig_f;
    micro.nu_fission =
      fissionable_ ? sig_f * this->nu(p.E(), EmissionMode::total) : 0.0;

    if (simulation::need_depletion_rx) {
      // Only non-zero reaction is (n,gamma)
      micro.reaction[0] = sig_a - sig_f;

      // Set all other reaction cross sections to zero
      for (int i = 1; i < DEPLETION_RX.size(); ++i) {
        micro.reaction[i] = 0.0;
      }
    }

    /*
     * index_temp, index_grid, and interp_factor are used only in the
     * following places:
     *   1. physics.cpp - scatter - For inelastic scatter.
     *   2. physics.cpp - sample_fission - For partial fissions.
     *   3. tallies/tally_scoring.cpp - score_general -
     *        For tallying on MTxxx reactions.
     *   4. nuclide.cpp - calculate_urr_xs - For unresolved purposes.
     * It is worth noting that none of these occur in the resolved resonance
     * range, so the value here does not matter.  index_temp is set to -1 to
     * force a segfault in case a developer messes up and tries to use it with
     * multipole.
     *
     * However, a segfault is not necessarily guaranteed with an out-of-bounds
     * access, so this technique should be replaced by something more robust
     * in the future.
     */
    micro.index_temp = -1;
    micro.index_grid = -1;
    micro.interp_factor = 0.0;

  } else {
    // Find the appropriate temperature index.
    double kT = p.sqrtkT() * p.sqrtkT();
    double f;
    int i_temp = -1;
    switch (settings::temperature_method) {
    case TemperatureMethod::NEAREST: {
      double max_diff = INFTY;
      for (int t = 0; t < kTs_.size(); ++t) {
        double diff = std::abs(kTs_[t] - kT);
        if (diff < max_diff) {
          i_temp = t;
          max_diff = diff;
        }
      }
    } break;

    case TemperatureMethod::INTERPOLATION:
      // If current kT outside of the bounds of available, snap to the bound
      if (kT < kTs_.front()) {
        i_temp = 0;
        break;
      }
      if (kT > kTs_.back()) {
        i_temp = kTs_.size() - 1;
        break;
      }

      // Find temperatures that bound the actual temperature
      for (i_temp = 0; i_temp < kTs_.size() - 1; ++i_temp) {
        if (kTs_[i_temp] <= kT && kT < kTs_[i_temp + 1])
          break;
      }

      // Randomly sample between temperature i and i+1
      f = (kT - kTs_[i_temp]) / (kTs_[i_temp + 1] - kTs_[i_temp]);
      if (f > prn(p.current_seed()))
        ++i_temp;
      break;
    }

    // Determine the energy grid index using a logarithmic mapping to
    // reduce the energy range over which a binary search needs to be
    // performed

    const auto& grid {grid_[i_temp]};
    const auto& xs {xs_[i_temp]};

    int i_grid;
    if (p.E() < grid.energy.front()) {
      i_grid = 0;
    } else if (p.E() > grid.energy.back()) {
      i_grid = grid.energy.size() - 2;
    } else {
      // Determine bounding indices based on which equal log-spaced
      // interval the energy is in
      int i_low = grid.grid_index[i_log_union];
      int i_high = grid.grid_index[i_log_union + 1] + 1;

      // Perform binary search over reduced range
      i_grid = i_low + lower_bound_index(
                         &grid.energy[i_low], &grid.energy[i_high], p.E());
    }

    // check for rare case where two energy points are the same
    if (grid.energy[i_grid] == grid.energy[i_grid + 1])
      ++i_grid;

    // calculate interpolation factor
    f = (p.E() - grid.energy[i_grid]) /
        (grid.energy[i_grid + 1] - grid.energy[i_grid]);

    micro.index_temp = i_temp;
    micro.index_grid = i_grid;
    micro.interp_factor = f;

    // Calculate microscopic nuclide total cross section
    micro.total =
      (1.0 - f) * xs(i_grid, XS_TOTAL) + f * xs(i_grid + 1, XS_TOTAL);

    // Calculate microscopic nuclide absorption cross section
    micro.absorption =
      (1.0 - f) * xs(i_grid, XS_ABSORPTION) + f * xs(i_grid + 1, XS_ABSORPTION);

    if (fissionable_) {
      // Calculate microscopic nuclide total cross section
      micro.fission =
        (1.0 - f) * xs(i_grid, XS_FISSION) + f * xs(i_grid + 1, XS_FISSION);

      // Calculate microscopic nuclide nu-fission cross section
      micro.nu_fission = (1.0 - f) * xs(i_grid, XS_NU_FISSION) +
                         f * xs(i_grid + 1, XS_NU_FISSION);
    } else {
      micro.fission = 0.0;
      micro.nu_fission = 0.0;
    }

    // Calculate microscopic nuclide photon production cross section
    micro.photon_prod = (1.0 - f) * xs(i_grid, XS_PHOTON_PROD) +
                        f * xs(i_grid + 1, XS_PHOTON_PROD);

    // Depletion-related reactions
    if (simulation::need_depletion_rx) {
      // Initialize all reaction cross sections to zero
      for (double& xs_i : micro.reaction) {
        xs_i = 0.0;
      }

      for (int j = 0; j < DEPLETION_RX.size(); ++j) {
        // If reaction is present and energy is greater than threshold, set
        // the reaction xs appropriately
        int i_rx = reaction_index_[DEPLETION_RX[j]];
        if (i_rx >= 0) {
          const auto& rx = reactions_[i_rx];
          const auto& rx_xs = rx->xs_[i_temp].value;

          // Physics says that (n,gamma) is not a threshold reaction, so we
          // don't need to specifically check its threshold index
          if (j == 0) {
            micro.reaction[0] =
              (1.0 - f) * rx_xs[i_grid] + f * rx_xs[i_grid + 1];
            continue;
          }

          int threshold = rx->xs_[i_temp].threshold;
          if (i_grid >= threshold) {
            micro.reaction[j] = (1.0 - f) * rx_xs[i_grid - threshold] +
                                f * rx_xs[i_grid - threshold + 1];
          } else if (j >= 3) {
            // One can show that the the threshold for (n,(x+1)n) is always
            // higher than the threshold for (n,xn). Thus, if we are below
            // the threshold for, e.g., (n,2n), there is no reason to check
            // the threshold for (n,3n) and (n,4n).
            break;
          }
        }
      }
    }
  }

  // Initialize sab treatment to false
  micro.index_sab = C_NONE;
  micro.sab_frac = 0.0;

  // Initialize URR probability table treatment to false
  micro.use_ptable = false;

  // If there is S(a,b) data for this nuclide, we need to set the
  // sab_scatter and sab_elastic cross sections and correct the total and
  // elastic cross sections.

  if (i_sab >= 0)
    this->calculate_sab_xs(i_sab, sab_frac, p);

  // If the particle is in the unresolved resonance range and there are
  // probability tables, we need to determine cross sections from the table
  if (settings::urr_ptables_on && urr_present_ && !use_mp) {
    if (urr_data_[micro.index_temp].energy_in_bounds(p.E()))
      this->calculate_urr_xs(micro.index_temp, p);
  }

  micro.last_E = p.E();
  micro.last_sqrtkT = p.sqrtkT();
}

void Nuclide::calculate_sab_xs(int i_sab, double sab_frac, Particle& p)
{
  auto& micro {p.neutron_xs(index_)};

  // Set flag that S(a,b) treatment should be used for scattering
  micro.index_sab = i_sab;

  // Calculate the S(a,b) cross section
  int i_temp;
  double elastic;
  double inelastic;
  data::thermal_scatt[i_sab]->calculate_xs(
    p.E(), p.sqrtkT(), &i_temp, &elastic, &inelastic, p.current_seed());

  // Store the S(a,b) cross sections.
  micro.thermal = sab_frac * (elastic + inelastic);
  micro.thermal_elastic = sab_frac * elastic;

  // Calculate free atom elastic cross section
  this->calculate_elastic_xs(p);

  // Correct total and elastic cross sections
  micro.total = micro.total + micro.thermal - sab_frac * micro.elastic;
  micro.elastic = micro.thermal + (1.0 - sab_frac) * micro.elastic;

  // Save temperature index and thermal fraction
  micro.index_temp_sab = i_temp;
  micro.sab_frac = sab_frac;
}

void Nuclide::calculate_urr_xs(int i_temp, Particle& p) const
{
  auto& micro = p.neutron_xs(index_);
  micro.use_ptable = true;

  // Create a shorthand for the URR data
  const auto& urr = urr_data_[i_temp];

  // Determine the energy table
  int i_energy =
    lower_bound_index(urr.energy_.begin(), urr.energy_.end(), p.E());

  // Sample the probability table using the cumulative distribution

  // Random numbers for the xs calculation are sampled from a separate stream.
  // This guarantees the randomness and, at the same time, makes sure we
  // reuse random numbers for the same nuclide at different temperatures,
  // therefore preserving correlation of temperature in probability tables.
  double r =
    future_prn(static_cast<int64_t>(index_), p.seeds(STREAM_URR_PTABLE));

  // Warning: this assumes row-major order of cdf_values_
  int i_low = upper_bound_index(&urr.cdf_values_(i_energy, 0),
                &urr.cdf_values_(i_energy, 0) + urr.n_cdf(), r) +
              1;
  int i_up = upper_bound_index(&urr.cdf_values_(i_energy + 1, 0),
               &urr.cdf_values_(i_energy + 1, 0) + urr.n_cdf(), r) +
             1;

  // Determine elastic, fission, and capture cross sections from the
  // probability table
  double elastic = 0.;
  double fission = 0.;
  double capture = 0.;
  double f;
  if (urr.interp_ == Interpolation::lin_lin) {
    // Determine the interpolation factor on the table
    f = (p.E() - urr.energy_[i_energy]) /
        (urr.energy_[i_energy + 1] - urr.energy_[i_energy]);

    elastic = (1. - f) * urr.xs_values_(i_energy, i_low).elastic +
              f * urr.xs_values_(i_energy + 1, i_up).elastic;
    fission = (1. - f) * urr.xs_values_(i_energy, i_low).fission +
              f * urr.xs_values_(i_energy + 1, i_up).fission;
    capture = (1. - f) * urr.xs_values_(i_energy, i_low).n_gamma +
              f * urr.xs_values_(i_energy + 1, i_up).n_gamma;
  } else if (urr.interp_ == Interpolation::log_log) {
    // Determine interpolation factor on the table
    f = std::log(p.E() / urr.energy_[i_energy]) /
        std::log(urr.energy_[i_energy + 1] / urr.energy_[i_energy]);

    // Calculate the elastic cross section/factor
    if ((urr.xs_values_(i_energy, i_low).elastic > 0.) &&
        (urr.xs_values_(i_energy + 1, i_up).elastic > 0.)) {
      elastic =
        std::exp((1. - f) * std::log(urr.xs_values_(i_energy, i_low).elastic) +
                 f * std::log(urr.xs_values_(i_energy + 1, i_up).elastic));
    } else {
      elastic = 0.;
    }

    // Calculate the fission cross section/factor
    if ((urr.xs_values_(i_energy, i_low).fission > 0.) &&
        (urr.xs_values_(i_energy + 1, i_up).fission > 0.)) {
      fission =
        std::exp((1. - f) * std::log(urr.xs_values_(i_energy, i_low).fission) +
                 f * std::log(urr.xs_values_(i_energy + 1, i_up).fission));
    } else {
      fission = 0.;
    }

    // Calculate the capture cross section/factor
    if ((urr.xs_values_(i_energy, i_low).n_gamma > 0.) &&
        (urr.xs_values_(i_energy + 1, i_up).n_gamma > 0.)) {
      capture =
        std::exp((1. - f) * std::log(urr.xs_values_(i_energy, i_low).n_gamma) +
                 f * std::log(urr.xs_values_(i_energy + 1, i_up).n_gamma));
    } else {
      capture = 0.;
    }
  }

  // Determine the treatment of inelastic scattering
  double inelastic = 0.;
  if (urr.inelastic_flag_ != C_NONE) {
    // get interpolation factor
    f = micro.interp_factor;

    // Determine inelastic scattering cross section
    Reaction* rx = reactions_[urr_inelastic_].get();
    int xs_index = micro.index_grid - rx->xs_[i_temp].threshold;
    if (xs_index >= 0) {
      inelastic = (1. - f) * rx->xs_[i_temp].value[xs_index] +
                  f * rx->xs_[i_temp].value[xs_index + 1];
    }
  }

  // Multiply by smooth cross-section if needed
  if (urr.multiply_smooth_) {
    calculate_elastic_xs(p);
    elastic *= micro.elastic;
    capture *= (micro.absorption - micro.fission);
    fission *= micro.fission;
  }

  // Check for negative values
  if (elastic < 0.) {
    elastic = 0.;
  }
  if (fission < 0.) {
    fission = 0.;
  }
  if (capture < 0.) {
    capture = 0.;
  }

  // Set elastic, absorption, fission, total, and capture x/s. Note that the
  // total x/s is calculated as a sum of partials instead of the
  // table-provided value
  micro.elastic = elastic;
  micro.absorption = capture + fission;
  micro.fission = fission;
  micro.total = elastic + inelastic + capture + fission;
  if (simulation::need_depletion_rx) {
    micro.reaction[0] = capture;
  }

  // Determine nu-fission cross-section
  if (fissionable_) {
    micro.nu_fission = nu(p.E(), EmissionMode::total) * micro.fission;
  }
}

std::pair<int64_t, double> Nuclide::find_temperature(double T) const
{
  assert(T >= 0.0);

  // Determine temperature index
  int64_t i_temp = 0;
  double f = 0.0;
  double kT = K_BOLTZMANN * T;
  int64_t n = kTs_.size();
  switch (settings::temperature_method) {
  case TemperatureMethod::NEAREST: {
    double max_diff = INFTY;
    for (int64_t t = 0; t < n; ++t) {
      double diff = std::abs(kTs_[t] - kT);
      if (diff < max_diff) {
        i_temp = t;
        max_diff = diff;
      }
    }
  } break;

  case TemperatureMethod::INTERPOLATION:
    // If current kT outside of the bounds of available, snap to the bound
    if (kT < kTs_.front()) {
      i_temp = 0;
      break;
    }
    if (kT > kTs_.back()) {
      i_temp = kTs_.size() - 1;
      break;
    }
    // Find temperatures that bound the actual temperature
    while (kTs_[i_temp + 1] < kT && i_temp + 1 < n - 1)
      ++i_temp;

    // Determine interpolation factor
    f = (kT - kTs_[i_temp]) / (kTs_[i_temp + 1] - kTs_[i_temp]);
  }

  assert(i_temp >= 0 && i_temp < n);

  return {i_temp, f};
}

double Nuclide::collapse_rate(int MT, double temperature,
  span<const double> energy, span<const double> flux) const
{
  assert(MT > 0);
  assert(energy.size() > 0);
  assert(energy.size() == flux.size() + 1);

  int i_rx = reaction_index_[MT];
  if (i_rx < 0)
    return 0.0;
  const auto& rx = reactions_[i_rx];

  // Determine temperature index
  int64_t i_temp;
  double f;
  std::tie(i_temp, f) = this->find_temperature(temperature);

  // Get reaction rate at lower temperature
  const auto& grid_low = grid_[i_temp].energy;
  double rr_low = rx->collapse_rate(i_temp, energy, flux, grid_low);

  if (f > 0.0) {
    // Interpolate between reaction rate at lower and higher temperature
    const auto& grid_high = grid_[i_temp + 1].energy;
    double rr_high = rx->collapse_rate(i_temp + 1, energy, flux, grid_high);
    return rr_low + f * (rr_high - rr_low);
  } else {
    // If interpolation factor is zero, return reaction rate at lower
    // temperature
    return rr_low;
  }
}

//==============================================================================
// Non-member functions
//==============================================================================

void check_data_version(hid_t file_id)
{
  if (attribute_exists(file_id, "version")) {
    vector<int> version;
    read_attribute(file_id, "version", version);
    if (version[0] != HDF5_VERSION[0]) {
      fatal_error("HDF5 data format uses version " +
                  std::to_string(version[0]) + "." +
                  std::to_string(version[1]) +
                  " whereas your installation of "
                  "OpenMC expects version " +
                  std::to_string(HDF5_VERSION[0]) + ".x data.");
    }
  } else {
    fatal_error("HDF5 data does not indicate a version. Your installation of "
                "OpenMC expects version " +
                std::to_string(HDF5_VERSION[0]) + ".x data.");
  }
}

extern "C" size_t nuclides_size()
{
  return data::nuclides.size();
}

//==============================================================================
// C API
//==============================================================================

extern "C" int openmc_load_nuclide(const char* name, const double* temps, int n)
{
  if (data::nuclide_map.find(name) == data::nuclide_map.end() ||
      data::nuclide_map.at(name) >= data::elements.size()) {
    LibraryKey key {Library::Type::neutron, name};
    const auto& it = data::library_map.find(key);
    if (it == data::library_map.end()) {
      set_errmsg(
        "Nuclide '" + std::string {name} + "' is not present in library.");
      return OPENMC_E_DATA;
    }

    // Get filename for library containing nuclide
    int idx = it->second;
    const auto& filename = data::libraries[idx].path_;
    write_message(6, "Reading {} from {}", name, filename);

    // Open file and make sure version is sufficient
    hid_t file_id = file_open(filename, 'r');
    check_data_version(file_id);

    // Read nuclide data from HDF5
    hid_t group = open_group(file_id, name);
    vector<double> temperature {temps, temps + n};
    data::nuclides.push_back(make_unique<Nuclide>(group, temperature));

    close_group(group);
    file_close(file_id);

    // Read multipole file into the appropriate entry on the nuclides array
    int i_nuclide = data::nuclide_map.at(name);
    if (settings::temperature_multipole)
      read_multipole_data(i_nuclide);

    // Read elemental data, if necessary
    if (settings::photon_transport) {
      auto element = to_element(name);
      if (data::element_map.find(element) == data::element_map.end() ||
          data::element_map.at(element) >= data::elements.size()) {
        // Read photon interaction data from HDF5 photon library
        LibraryKey key {Library::Type::photon, element};
        const auto& it = data::library_map.find(key);
        if (it == data::library_map.end()) {
          set_errmsg("Element '" + std::string {element} +
                     "' is not present in library.");
          return OPENMC_E_DATA;
        }

        int idx = it->second;
        const auto& filename = data::libraries[idx].path_;
        write_message(6, "Reading {} from {} ", element, filename);

        // Open file and make sure version is sufficient
        hid_t file_id = file_open(filename, 'r');
        check_data_version(file_id);

        // Read element data from HDF5
        hid_t group = open_group(file_id, element.c_str());
        data::elements.push_back(make_unique<PhotonInteraction>(group));

        close_group(group);
        file_close(file_id);
      }
    }
  }
  return 0;
}

extern "C" int openmc_get_nuclide_index(const char* name, int* index)
{
  auto it = data::nuclide_map.find(name);
  if (it == data::nuclide_map.end()) {
    set_errmsg(
      "No nuclide named '" + std::string {name} + "' has been loaded.");
    return OPENMC_E_DATA;
  }
  *index = it->second;
  return 0;
}

extern "C" int openmc_nuclide_name(int index, const char** name)
{
  if (index >= 0 && index < data::nuclides.size()) {
    *name = data::nuclides[index]->name_.data();
    return 0;
  } else {
    set_errmsg("Index in nuclides vector is out of bounds.");
    return OPENMC_E_OUT_OF_BOUNDS;
  }
}

extern "C" int openmc_nuclide_collapse_rate(int index, int MT,
  double temperature, const double* energy, const double* flux, int n,
  double* xs)
{
  if (index < 0 || index >= data::nuclides.size()) {
    set_errmsg("Index in nuclides vector is out of bounds.");
    return OPENMC_E_OUT_OF_BOUNDS;
  }

  try {
    *xs = data::nuclides[index]->collapse_rate(
      MT, temperature, {energy, energy + n + 1}, {flux, flux + n});
  } catch (const std::out_of_range& e) {
    fmt::print("Caught error\n");
    set_errmsg(e.what());
    return OPENMC_E_OUT_OF_BOUNDS;
  }
  return 0;
}

void nuclides_clear()
{
  data::nuclides.clear();
  data::nuclide_map.clear();
}

bool multipole_in_range(const Nuclide& nuc, double E)
{
  return E >= nuc.multipole_->E_min_ && E <= nuc.multipole_->E_max_;
}

} // namespace openmc
