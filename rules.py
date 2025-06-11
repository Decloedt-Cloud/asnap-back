import json
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Dict, Optional


logger = logging.getLogger(__name__)

TARIF_REFERENCE_SEANCE = 120
TARIF_NUITEE = 1500
# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("assurance-rules")


@dataclass
class CategoryResult:
    name: str
    color: str  # Vert, Orange, Rouge
    details: Dict


@dataclass
class InsuranceAnalysis:
    overall_medal: str  # Gold, Silver, Bronze
    categories: List[CategoryResult]


class InsuranceAnalyzer:
    def __init__(self):
        self.categories = ["Médecine naturelle", "Hospitalisation", "Voyage", "Ambulatoire", "Accident", "Dentaire"]
        self.optional_categories = ["Accident", "Médecine naturelle", "Voyage"]
        self.results = []

    def analyze_pdf(self, pdf_data: Dict) -> InsuranceAnalysis:
        """Analyse les données extraites du PDF."""
        self.results = []

        # Extract birth date for age calculations
        birth_date = pdf_data.get("birth_date", "2000-01-01")
        logger.info(f"Date de naissance extraite: {birth_date}")

        # Analyse de chaque catégorie
        for category in self.categories:
            if category == "Médecine naturelle":
                result = self.analyze_medecine_naturelle(pdf_data.get("medecine_naturelle", {}))
            elif category == "Hospitalisation":
                result = self.analyze_hospitalisation(pdf_data.get("hospitalisation", {}))
            elif category == "Voyage":
                result = self.analyze_voyage(pdf_data.get("voyage", {}))
            elif category == "Ambulatoire":
                result = self.analyze_ambulatoire(pdf_data.get("ambulatoire", {}))
            elif category == "Accident":
                result = self.analyze_accident(pdf_data.get("accident", {}))
            elif category == "Dentaire":
                result = self.analyze_dentaire(pdf_data.get("dentaire", {}), birth_date)

            self.results.append(result)
            logger.info(
                f"Catégorie '{category}': {result.color} - Détails: {json.dumps(result.details, ensure_ascii=False)}")

        # Déterminer la médaille globale
        overall_medal = self.calculate_overall_medal()
        logger.info(f"Médaille globale: {overall_medal}")
        return InsuranceAnalysis(overall_medal=overall_medal, categories=self.results)

    def analyze_medecine_naturelle(self, data: Dict) -> CategoryResult:
        """Analyse la catégorie Médecine naturelle."""

        # Étendue peut être donnée directement (en %) ou calculée à partir d’un forfait
        etendue = data.get("etendue")  # % direct
        montant_par_seance = data.get("montant_par_seance")  # Si donné sous forme de forfait

        if etendue is None and montant_par_seance is not None:
            etendue = (montant_par_seance / TARIF_REFERENCE_SEANCE) * 100
            etendue = round(etendue, 2)
        elif etendue is None:
            etendue = 0

        plafond = data.get("plafond", 0)  # Nombre de séances
        franchise = data.get("franchise", 0)  # CHF

        logger.info(f"Médecine naturelle - Étendue: {etendue}%, Plafond: {plafond} séances, Franchise: {franchise} CHF")

        # Vert / Gold
        if etendue >= 80 and plafond >= 20 and franchise == 0:
            return CategoryResult("Médecine naturelle", "Vert",
                                  {"etendue": etendue, "plafond": plafond, "franchise": franchise})

        # Orange / Silver
        elif etendue >= 50 and etendue < 80 and plafond >= 10 and plafond < 20 and franchise < 200:
            return CategoryResult("Médecine naturelle", "Orange",
                                  {"etendue": etendue, "plafond": plafond, "franchise": franchise})

        # Rouge / Bronze
        return CategoryResult("Médecine naturelle", "Rouge",
                              {"etendue": etendue, "plafond": plafond, "franchise": franchise})

    def analyze_hospitalisation(self, data: Dict) -> CategoryResult:
        """Analyse la catégorie Hospitalisation, avec gestion des cas particuliers (ex. franchise volontaire chez KPT)."""

        type_prestation = data.get("type", "commune").lower()
        etendue = data.get("etendue", 0)
        franchise = data.get("franchise", 0)
        compagnie = data.get("compagnie", "").lower()
        franchise_volontaire = data.get("franchise_volontaire", False)

        # Conversion CHF/jour → %
        if etendue > 100:
            etendue_percent = (etendue / TARIF_NUITEE) * 100
        else:
            etendue_percent = etendue

        logger.info(
            f"Hospitalisation - Compagnie: {compagnie}, Type: {type_prestation}, Étendue: {etendue} ({etendue_percent}%), Franchise: {franchise} CHF")

        # ✅ Cas particulier : KPT + franchise volontaire → considérer comme ORANGE au minimum
        if compagnie == "kpt" and franchise_volontaire:
            if type_prestation == "privé" and etendue_percent <= 0:
                return CategoryResult("Hospitalisation", "Vert",
                                      {"cas_particulier": "KPT + franchise volontaire",
                                       "type": type_prestation, "etendue_percent": etendue_percent,
                                       "franchise": franchise})
            elif type_prestation == "semi-privé" and etendue_percent <= 10:
                return CategoryResult("Hospitalisation", "Orange",
                                      {"cas_particulier": "KPT + franchise volontaire",
                                       "type": type_prestation, "etendue_percent": etendue_percent,
                                       "franchise": franchise})

        # 💚 Couverture complète
        if type_prestation == "privé" and etendue_percent <= 0 and franchise == 0:
            return CategoryResult("Hospitalisation", "Vert",
                                  {"type": type_prestation, "etendue_percent": etendue_percent, "franchise": franchise})

        # 🟠 Couverture correcte
        elif type_prestation == "semi-privé" and etendue_percent <= 10:
            return CategoryResult("Hospitalisation", "Orange",
                                  {"type": type_prestation, "etendue_percent": etendue_percent, "franchise": franchise})

        # 🔴 Cas général : couverture limitée
        return CategoryResult("Hospitalisation", "Rouge",
                              {"type": type_prestation, "etendue_percent": etendue_percent, "franchise": franchise})


    def analyze_voyage(self, data: Dict) -> CategoryResult:
        """Analyse la catégorie Voyage."""
        urgence = data.get("traitement_urgence", False)
        rapatriement = data.get("rapatriement", False)
        annulation = data.get("annulation", False)

        logger.info(f"Voyage - Urgence: {urgence}, Rapatriement: {rapatriement}, Annulation: {annulation}")

        # Gold (Vert): Tous les critères présents
        if urgence and rapatriement and annulation:
            return CategoryResult("Voyage", "Vert",
                                  {"urgence": urgence, "rapatriement": rapatriement, "annulation": annulation})

        # Silver (Orange): Deux critères présents (sans annulation)
        elif urgence and rapatriement and not annulation:
            return CategoryResult("Voyage", "Orange",
                                  {"urgence": urgence, "rapatriement": rapatriement, "annulation": annulation})

        # Bronze (Rouge): Moins de deux critères
        return CategoryResult("Voyage", "Rouge",
                              {"urgence": urgence, "rapatriement": rapatriement, "annulation": annulation})

    def analyze_ambulatoire(self, data: Dict) -> CategoryResult:
        """Analyse la catégorie Ambulatoire (lunettes, psychothérapie, médicaments, transport, sauvetage)."""
        prestations = data.get("prestations", {})
        participation = data.get("participation", 0)  # % de participation financière

        required_keys = ["lunettes", "psychotherapie", "medicaments_hors_liste", "transport", "sauvetage"]

        # Complète les clés manquantes comme 'limité' (par défaut)
        for key in required_keys:
            if key not in prestations:
                prestations[key] = "absent"  # plus explicite que "limité" ici

        logger.info(f"Ambulatoire - Prestations: {prestations}, Participation: {participation}%")

        values = list(prestations.values())

        # Cas Vert
        if all(v == "illimité" for v in values) and participation <= 10:
            return CategoryResult("Ambulatoire", "Vert",
                                  {"prestations": prestations, "participation": participation})

        # Cas Orange : toutes illimitées mais quote-part > 10%
        if all(v == "illimité" for v in values) and participation > 10:
            return CategoryResult("Ambulatoire", "Orange",
                                  {"prestations": prestations, "participation": participation})

        # Cas Orange : toutes limitées et quote-part ≤ 10%
        if all(v == "limité" for v in values) and participation <= 10:
            return CategoryResult("Ambulatoire", "Orange",
                                  {"prestations": prestations, "participation": participation})

        # Cas Rouge : limitées + participation > 10%
        if all(v in ["limité", "illimité"] for v in values) and participation > 10:
            return CategoryResult("Ambulatoire", "Rouge",
                                  {"prestations": prestations, "participation": participation})

        # Cas Rouge : une ou plusieurs prestations absentes
        if any(v == "absent" for v in values):
            return CategoryResult("Ambulatoire", "Rouge",
                                  {"prestations": prestations, "participation": participation})

        # Par défaut (sécurité)
        return CategoryResult("Ambulatoire", "Rouge",
                              {"prestations": prestations, "participation": participation})

    def analyze_accident(self, data: Dict) -> CategoryResult:
        """Analyse la catégorie Accident."""
        clinique = data.get("clinique_privee", False)
        prestations_sup = data.get("prestations_supplementaires", False)
        capital_deces = data.get("capital_deces_invalidite", False)

        logger.info(
            f"Accident - Clinique privée: {clinique}, Prestations supp: {prestations_sup}, Capital décès: {capital_deces}")

        # Gold (Vert): Tous les critères présents
        if clinique and prestations_sup and capital_deces:
            return CategoryResult("Accident", "Vert",
                                  {"clinique": clinique, "prestations_sup": prestations_sup,
                                   "capital_deces": capital_deces})

        # Silver (Orange): Seule la clinique privée est couverte
        elif clinique and not (prestations_sup or capital_deces):
            return CategoryResult("Accident", "Orange",
                                  {"clinique": clinique, "prestations_sup": prestations_sup,
                                   "capital_deces": capital_deces})

        # Bronze (Rouge): Aucun critère ou seulement prestations supplémentaires/capital
        return CategoryResult("Accident", "Rouge",
                              {"clinique": clinique, "prestations_sup": prestations_sup,
                               "capital_deces": capital_deces})

    def analyze_dentaire(self, data: Dict, birth_date: str) -> CategoryResult:
        """Analyse la catégorie Dentaire."""
        etendue = data.get("etendue", 0)  # %
        plafond = data.get("plafond", 0)  # CHF
        franchise = data.get("franchise", 0)  # CHF
        orthodontie = data.get("orthodontie", 0)  # CHF

        # Calcul de l'âge pour l'orthodontie
        is_child = False
        try:
            birth_dt = datetime.strptime(birth_date, "%Y-%m-%d")
            age = (datetime.now() - birth_dt).days // 365
            is_child = age < 12
            logger.info(f"Âge calculé: {age} ans (enfant: {is_child})")
        except Exception as e:
            logger.error(f"Erreur calcul âge: {e}")
            is_child = False

        logger.info(
            f"Dentaire - Étendue: {etendue}%, Plafond: {plafond} CHF, Franchise: {franchise} CHF, Orthodontie: {orthodontie} CHF, Enfant: {is_child}")

        # Règle spéciale: Enfant sans couverture orthodontique suffisante = Rouge
        if is_child and orthodontie < 10000:
            return CategoryResult("Dentaire", "Rouge",
                                  {"etendue": etendue, "plafond": plafond, "franchise": franchise,
                                   "orthodontie": orthodontie, "is_child": is_child})

        # Gold (Vert): Étendue >= 75%, Plafond >= 3000, Franchise = 0
        if etendue >= 75 and plafond >= 3000 and franchise == 0:
            return CategoryResult("Dentaire", "Vert",
                                  {"etendue": etendue, "plafond": plafond, "franchise": franchise,
                                   "orthodontie": orthodontie, "is_child": is_child})

        # Silver (Orange): Étendue >= 50%, Plafond >= 1000, Franchise < 200
        elif etendue >= 50 and plafond >= 1000 and franchise < 200:
            return CategoryResult("Dentaire", "Orange",
                                  {"etendue": etendue, "plafond": plafond, "franchise": franchise,
                                   "orthodontie": orthodontie, "is_child": is_child})

        # Bronze (Rouge): Autres cas
        return CategoryResult("Dentaire", "Rouge",
                              {"etendue": etendue, "plafond": plafond, "franchise": franchise,
                               "orthodontie": orthodontie, "is_child": is_child})

    def calculate_overall_medal(self) -> str:
        """Calcule la médaille globale en fonction des résultats."""
        orange_count = sum(1 for r in self.results if r.color == "Orange")
        rouge_count = sum(1 for r in self.results if r.color == "Rouge")

        logger.info(f"Comptage: Orange={orange_count}, Rouge={rouge_count}")

        # Gold: Aucun Orange ou Rouge
        if rouge_count == 0 and orange_count == 0:
            return "Gold"

        # Silver: Maximum 1 Rouge OU maximum 3 Orange
        elif rouge_count <= 1 and orange_count <= 3:
            return "Silver"

        # Bronze: Autres cas
        return "Bronze"

    def rectify_analysis(self, optional_exclusions: List[str]) -> InsuranceAnalysis:
        """Permet de rectifier l'analyse en excluant des catégories facultatives."""
        logger.info(f"Rectification avec exclusions: {optional_exclusions}")

        # Filtrer les catégories exclues
        filtered_results = [r for r in self.results if r.name not in optional_exclusions]
        self.results = filtered_results

        # Recalculer la médaille globale
        overall_medal = self.calculate_overall_medal()
        return InsuranceAnalysis(overall_medal=overall_medal, categories=self.results)


# Example usage (for testing)
if __name__ == "__main__":
    analyzer = InsuranceAnalyzer()
